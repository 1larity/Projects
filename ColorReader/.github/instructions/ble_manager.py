# ble_manager.py
import asyncio
import threading
from time import monotonic
from typing import Callable, List, Tuple, Optional
from bleak import BleakScanner, BleakClient

# Vendor service + interesting characteristics
VENDOR_SVC = "da2b84f1-6279-48de-bdc0-afbea0226079"
MEASURE_PRIME_UUID = "a87988b9-694c-479c-900e-95dfa6c00a24"   # write+read
MEASURE_TRIG_UUID  = "bf03260c-7205-4c25-af43-93b1c299d159"   # write-only
MEASURE_RET_UUID   = "fdd6b4d3-046d-4330-bdec-1fd0c90cb43b"   # notify/indicate
MEASURE_ALT_UUID   = "0a1934f5-24b8-4f13-9842-37bb167c6aff"   # write/wr-no-rsp/read (echo/status)

FORCE_SUBSCRIBE_UUIDS = [MEASURE_RET_UUID, "18cda784-4bd3-4370-85bb-bfed91ec86af"]

READ_THROTTLE_MS = 80
READ_MAX_BYTES_LOG = 64

class BleManager:
    """
    Runs a dedicated asyncio loop/thread for BLE. UI calls into this manager
    from any thread; we marshal work to the BLE loop with call_soon_threadsafe.
    """

    def __init__(self, log_cb: Optional[Callable[[str], None]] = None) -> None:
        self._log_cb = log_cb or (lambda s: None)
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._loop_thread.start()

        # BLE state
        self.client: Optional[BleakClient] = None
        self.connected_address: Optional[str] = None
        self.ready: bool = False

        # Service cache (lowercased uuids)
        self.discovered_chars: List[Tuple[str, List[str]]] = []   # (uuid, props)
        self.char_service_map = {}  # uuid -> service uuid

        # Gen to invalidate old callbacks if reconnect happens
        self._connect_gen = 0

        # Notify recency
        self._last_notify_uuid = ""
        self._last_notify_t = 0.0

        # Recent log buffer also available to UI (read-only copy)
        self._log_buf: List[str] = []

    # ---------- logging ----------
    def log(self, s: str) -> None:
        self._log_buf.append(s)
        # Trim buffer to avoid unbounded growth
        if len(self._log_buf) > 5000:
            self._log_buf = self._log_buf[-2000:]
        self._log_cb(s)

    def get_log_snapshot(self) -> List[str]:
        return list(self._log_buf)

    # ---------- threading helpers ----------
    def run_coro(self, coro):
        """Schedule an awaitable on the BLE loop."""
        if self._loop.is_closed():
            # defensive: should not happen during app lifetime
            self.log("BLE loop already closed.")
            return
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    # ---------- helpers ----------
    async def _is_live(self) -> bool:
        try:
            return bool(self.client) and await self.client.is_connected()
        except Exception:
            return False

    async def _ensure_live_or_raise(self) -> None:
        if not self.client or not await self._is_live():
            raise RuntimeError("Not connected")

    def _notify_cb(self, sender, data: bytes):
        try:
            uid = str(sender).lower()
        except Exception:
            uid = f"{sender}".lower()
        self._last_notify_uuid = uid
        self._last_notify_t = monotonic()
        hexp = data.hex() if isinstance(data, (bytes, bytearray)) else str(data)
        self.log(f"Notification from {uid}: {hexp}")

    async def _safe_start_notify(self, uuid: str):
        if not await self._is_live():
            self.log(f"Notify skipped {uuid}: Not connected")
            return
        try:
            await self.client.start_notify(uuid, self._notify_cb)
            self.log(f"Subscribed to {uuid}")
        except Exception as e:
            self.log(f"Subscribe {uuid} skipped: {e}")

    async def _dump_descriptors(self):
        """Best-effort CCCD readout to confirm 0100/0200 vs 0000."""
        try:
            if not self.client or not self.client.services:
                return
            for svc in self.client.services:
                for ch in svc.characteristics:
                    for d in ch.descriptors:
                        info = f"Desc {d.uuid} on {ch.uuid} handle={getattr(d,'handle',None)}"
                        try:
                            val = await self.client.read_gatt_descriptor(d.handle)
                            hexv = val.hex() if isinstance(val, (bytes, bytearray)) else str(val)
                            self.log(f"{info} val={hexv}")
                        except Exception as e:
                            self.log(f"{info} read_err={e}")
        except Exception as e:
            self.log(f"dump_descriptors error: {e}")

    async def _refresh_services(self):
        """Refetch services into local caches. Called after connect and on services-changed."""
        self.discovered_chars.clear()
        self.char_service_map.clear()

        if not self.client:
            return

        services = await self.client.get_services()
        for service in services:
            svc_uuid = str(service.uuid).lower()
            self.log(f"Service: {service.uuid}")
            for ch in service.characteristics:
                cu = str(ch.uuid).lower()
                props = list(ch.properties)
                self.log(f"  Characteristic: {ch.uuid} (Properties: {props})")
                self.discovered_chars.append((cu, props))
                self.char_service_map[cu] = svc_uuid

    # ---------- public API (UI calls these) ----------
    def scan(self):
        async def _scan():
            out = []
            try:
                devices = await BleakScanner.discover()
                for d in devices:
                    out.append(f"{d.name or 'Unknown'} ({d.address})")
            except Exception as e:
                out.append(f"Error: {e}")
            self.log(f"Scan found {len(out)} device(s).")
            # return via log; UI can call get_scan_results if you store it
            self._last_scan = out  # stash for UI
        self._last_scan: List[str] = []
        self.run_coro(_scan())

    def get_scan_results(self) -> List[str]:
        return getattr(self, "_last_scan", [])

    def connect(self, address: str, do_pair: bool = False):
        async def _connect():
            self.ready = False
            self._connect_gen += 1
            my_gen = self._connect_gen

            # Close previous
            try:
                if self.client and await self._is_live():
                    await self.client.disconnect()
            except Exception:
                pass

            # Use cached_services=False on WinRT to avoid stale DB
            try:
                self.client = BleakClient(address, winrt={"use_cached_services": False})
            except TypeError:
                # non-Windows backend or older Bleak
                self.client = BleakClient(address)

            # Disconnected callback
            def _on_dc(_):
                # Only consider if still current
                if my_gen == self._connect_gen:
                    self.ready = False
                    self.log(f"Disconnected from {address}")

            try:
                self.client.set_disconnected_callback(_on_dc)
            except Exception:
                pass

            try:
                await self.client.connect()
                self.connected_address = address
                self.log(f"Connected to {address}")
            except Exception as e:
                self.connected_address = None
                self.client = None
                self.log(f"Connect failed: {e}")
                return

            # (Optional) Pair/bond
            if do_pair:
                try:
                    if hasattr(self.client, "pair"):
                        ok = await self.client.pair()
                        self.log(f"Pair result: {ok}")
                except Exception as e:
                    self.log(f"Pair failed: {e}")

            # If link dropped during pair, bail
            if not await self._is_live():
                self.log("Link not live after connect/pair; aborting setup.")
                return

            # Populate services/characteristics
            try:
                await self._refresh_services()
            except Exception as e:
                self.log(f"Service discovery failed: {e}")

            # Auto-subscribe (skip GAP/GATT, and skip 0x2A05)
            try:
                for cu, props in list(self.discovered_chars):
                    svc_uuid = self.char_service_map.get(cu, "")
                    if (("notify" in props) or ("indicate" in props)) and \
                        svc_uuid not in ("00001800-0000-1000-8000-00805f9b34fb",
                                         "00001801-0000-1000-8000-00805f9b34fb") and \
                        cu != "00002a05-0000-1000-8000-00805f9b34fb":
                        await self._safe_start_notify(cu)
            except Exception as e:
                self.log(f"Auto-subscribe error: {e}")

            # Force vendor subscribes and dump CCCDs
            await asyncio.sleep(0.1)
            for u in FORCE_SUBSCRIBE_UUIDS:
                await self._safe_start_notify(u)
            await self._dump_descriptors()

            # Index print (user-friendly)
            for i, (uu, pp) in enumerate(self.discovered_chars):
                self.log(f"[{i}] {uu} {pp}")

            # Mark ready now
            if my_gen == self._connect_gen and await self._is_live():
                self.ready = True
                self.log("READY")

        self.run_coro(_connect())

    def disconnect(self):
        async def _dc():
            try:
                if self.client and await self._is_live():
                    await self.client.disconnect()
            except Exception as e:
                self.log(f"Disconnect error: {e}")
            finally:
                self.ready = False
                self.client = None
                self.connected_address = None
        self.run_coro(_dc())

    # -------- actions ----------
    def read_all_readable(self):
        async def _scan_reads():
            if not await self._is_live():
                self.log("Skip read-all: not connected.")
                return
            for uu, props in list(self.discovered_chars):
                if "read" not in set(props):
                    continue
                if not await self._is_live():
                    self.log("Read-all aborted: link down.")
                    break
                try:
                    data = await self.client.read_gatt_char(uu)
                    hexv = data.hex() if isinstance(data, (bytes, bytearray)) else str(data)
                    if isinstance(data, (bytes, bytearray)) and len(data) > READ_MAX_BYTES_LOG:
                        hexv = f"{data[:READ_MAX_BYTES_LOG].hex()}...(+{len(data)-READ_MAX_BYTES_LOG}B)"
                    self.log(f"READ {uu}: {hexv}")
                except Exception as e:
                    self.log(f"READ error {uu}: {e}")
                await asyncio.sleep(READ_THROTTLE_MS / 1000.0)
        if self.ready:
            self.run_coro(_scan_reads())
        else:
            self.log("Skip: not ready.")

    def read_char(self, sel: int | str):
        async def _r():
            await self._ensure_live_or_raise()
            uuid = self._resolve_sel(sel)
            props = self._props_for(uuid)
            if "read" not in props:
                self.log(f"READ not allowed on {uuid}. Props={sorted(list(props))}")
                return
            try:
                data = await self.client.read_gatt_char(uuid)
                self.log(f"READ {uuid}: {data.hex()} | {data!r}")
            except Exception as e:
                self.log(f"READ error {uuid}: {e}")
        self.run_coro(_r())

    def write_char(self, sel: int | str, payload: bytes, response: Optional[bool] = None):
        async def _w():
            await self._ensure_live_or_raise()
            uuid = self._resolve_sel(sel)
            props = self._props_for(uuid)
            if "write" not in props and "write-without-response" not in props:
                self.log(f"WRITE not allowed on {uuid}. Props={sorted(list(props))}")
                return
            use_resp = ("write-without-response" not in props) if response is None else response
            try:
                await self.client.write_gatt_char(uuid, payload, response=use_resp)
                self.log(f"WROTE {uuid}: {payload.hex()} (resp={use_resp})")
            except Exception as e:
                self.log(f"WRITE error {uuid}: {e}")
        self.run_coro(_w())

    def set_notify(self, sel: int | str, enable: bool = True):
        async def _n():
            await self._ensure_live_or_raise()
            uuid = self._resolve_sel(sel)
            try:
                if enable:
                    await self.client.start_notify(uuid, self._notify_cb)
                    self.log(f"NOTIFY ON {uuid}")
                else:
                    await self.client.stop_notify(uuid)
                    self.log(f"NOTIFY OFF {uuid}")
            except Exception as e:
                self.log(f"NOTIFY error {uuid}: {e}")
        self.run_coro(_n())

    # -------- utility lookups ----------
    def _resolve_sel(self, sel: int | str) -> str:
        if isinstance(sel, int) and 0 <= sel < len(self.discovered_chars):
            return self.discovered_chars[sel][0]
        return str(sel).strip().lower()

    def _props_for(self, uuid_str: str) -> set:
        u = uuid_str.lower()
        for uu, props in self.discovered_chars:
            if uu == u:
                return set(props)
        return set()

    # -------- shutdown ----------
    def close(self):
        """Call on app shutdown."""
        def _stop():
            # best-effort disconnect
            async def _dc():
                try:
                    if self.client and await self._is_live():
                        await self.client.disconnect()
                except Exception:
                    pass
            fut = asyncio.run_coroutine_threadsafe(_dc(), self._loop)
            try:
                fut.result(timeout=1.0)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)

        _stop()
        self._loop_thread.join(timeout=2.0)
