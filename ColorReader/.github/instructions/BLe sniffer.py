import pygame
import asyncio
import threading
from bleak import BleakScanner, BleakClient
import pyperclip
import logging
from time import monotonic

# -------------------- Single global asyncio loop thread --------------------
_LOOP = asyncio.new_event_loop()
def _loop_runner():
    asyncio.set_event_loop(_LOOP)
    _LOOP.run_forever()
_loop_thread = threading.Thread(target=_loop_runner, daemon=True)
_loop_thread.start()

def run_coro(coro):
    """Schedule an async coroutine on the single loop and return a concurrent.futures.Future."""
    return asyncio.run_coroutine_threadsafe(coro, _LOOP)

# -------------------- Pygame/UI setup --------------------
pygame.init()

HELP_TEXT = ("Keys: 0–9 select characteristic | R=read | W=write | "
             "N=notify on | U=notify off | P=probe vendor | M=measure attempt | "
             "A=scan readable | D=dump desc | Wheel=scroll log | B=brute force | Shift+B=alt BF")

CONNECT_GEN = 0
SERVICES_READY = False

MEASURE_PRIME_UUID = "a87988b9-694c-479c-900e-95dfa6c00a24"   # write+read
MEASURE_TRIG_UUID  = "bf03260c-7205-4c25-af43-93b1c299d159"   # write-only
MEASURE_RET_UUID   = "fdd6b4d3-046d-4330-bdec-1fd0c90cb43b"   # notify/indicate
MEASURE_ALT_UUID   = "0a1934f5-24b8-4f13-9842-37bb167c6aff"   # write/wr-no-rsp/read (echo/status)
LAST_NOTIFY_UUID = ""
LAST_NOTIFY_T = 0.0
FORCE_SUBSCRIBE_UUIDS = [MEASURE_RET_UUID, "18cda784-4bd3-4370-85bb-bfed91ec86af"]

discovered_chars = []          # [(uuid, properties)]
selected_char_index = -1
connected_client = None
stop_event = threading.Event()
AUTO_SCAN_READS = False         # safer default; press 'A' to scan
READ_THROTTLE_MS = 250          # gentler on flaky stacks
READ_MAX_BYTES_LOG = 64
char_service_map = {}  # uuid -> service_uuid

# Screen
SCREEN_WIDTH, SCREEN_HEIGHT = 800, 600
WHITE, BLACK, BLUE, GRAY, RED, LIGHT_GRAY = (255,255,255), (0,0,0), (0,122,255), (200,200,200), (255,0,0), (220,220,220)
pygame.font.init()
FONT = pygame.font.SysFont("Arial", 24)
SMALL_FONT = pygame.font.SysFont("Arial", 18)
screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
pygame.display.set_caption("ColorReader BLE Scanner")

# Layout
LIST_X, LIST_Y, LIST_WIDTH, LIST_HEIGHT = 50, 50, 700, 200
list_rect = pygame.Rect(LIST_X, LIST_Y, LIST_WIDTH, LIST_HEIGHT)
BUTTON_WIDTH, BUTTON_HEIGHT = 220, 40
BUTTON_Y = LIST_Y + LIST_HEIGHT + 10
button_rect = pygame.Rect((SCREEN_WIDTH // 2 - BUTTON_WIDTH // 2, BUTTON_Y), (BUTTON_WIDTH, BUTTON_HEIGHT))
LOG_X, LOG_Y, LOG_WIDTH, LOG_HEIGHT = 50, BUTTON_Y + BUTTON_HEIGHT + 10, 700, 210
log_rect = pygame.Rect(LOG_X, LOG_Y, LOG_WIDTH, LOG_HEIGHT)

# State
scanned_devices = []
scanning = False
selected_device_index = -1
connected_device = None
traffic_log = []
log_scroll_offset = 0

# Logging
open("traffic_log.txt", "w").close()
logging.basicConfig(filename="traffic_log.txt", level=logging.INFO, format="%(asctime)s - %(message)s")

def log_message(message: str):
    global traffic_log
    traffic_log.append(message)
    logging.info(message)

# -------------------- Async helpers --------------------
async def _is_live(c) -> bool:
    try:
        return await c.is_connected()
    except Exception:
        return False

async def _safe_notify(c, uuid: str):
    if not await _is_live(c):
        log_message(f"Notify skipped {uuid}: Not connected")
        return
    try:
        await c.start_notify(uuid, notification_handler)
        log_message(f"  Subscribed to notifications for {uuid}")
    except Exception as e:
        log_message(f"  Notify skipped {uuid}: {e}")

async def scan_devices():
    global scanned_devices, scanning
    scanned_devices = []
    scanning = True
    try:
        devices = await BleakScanner.discover()
        for device in devices:
            scanned_devices.append(f"{device.name or 'Unknown'} ({device.address})")
    except Exception as e:
        scanned_devices.append(f"Error: {str(e)}")
    finally:
        scanning = False

async def dump_descriptors(c: BleakClient):
    try:
        services = c.services if hasattr(c, "services") and c.services else (await c.get_services())
        for svc in services:
            for ch in svc.characteristics:
                for d in ch.descriptors:
                    info = f"Desc {d.uuid} on {ch.uuid} handle={getattr(d,'handle',None)}"
                    try:
                        val = await c.read_gatt_descriptor(d.handle)
                        hexv = val.hex() if isinstance(val,(bytes,bytearray)) else str(val)
                        log_message(f"{info} val={hexv}")
                    except Exception as e:
                        log_message(f"{info} read_err={e}")
    except Exception as e:
        log_message(f"dump_descriptors error: {e}")

async def ensure_vendor_notifies(client: BleakClient):
    for u in FORCE_SUBSCRIBE_UUIDS:
        try:
            await client.start_notify(u, notification_handler)
            log_message(f"Subscribed to {u}")
        except Exception as e:
            log_message(f"Subscribe {u} skipped: {e}")
    await dump_descriptors(client)

async def refresh_services(client: BleakClient):
    global SERVICES_READY, discovered_chars, char_service_map
    SERVICES_READY = False
    services = await client.get_services()
    discovered_chars.clear()
    char_service_map.clear()
    for service in services:
        svc_uuid = str(service.uuid).lower()
        for ch in service.characteristics:
            cu = str(ch.uuid).lower()
            discovered_chars.append((cu, list(ch.properties)))
            char_service_map[cu] = svc_uuid
    SERVICES_READY = True

async def safe_refresh(client: BleakClient):
    try:
        if await _is_live(client):
            await refresh_services(client)
            log_message("Services refreshed.")
    except Exception as e:
        log_message(f"Service refresh failed: {e}")

async def _keepalive(c: BleakClient):
    while not stop_event.is_set() and await _is_live(c):
        try:
            await asyncio.sleep(10)
            if await _is_live(c):
                try:
                    await c.read_gatt_char("00002a19-0000-1000-8000-00805f9b34fb")  # Battery if present
                except Exception:
                    pass
        except Exception:
            break

async def connect_to_device(address: str):
    global connected_device, traffic_log, discovered_chars, selected_char_index, connected_client, SERVICES_READY, CONNECT_GEN
    traffic_log = []
    discovered_chars = []
    selected_char_index = -1
    retries = 3

    for attempt in range(retries):
        client = None
        try:
            client = BleakClient(address)
            await client.connect()
            connected_client = client
            connected_device = address
            log_message(f"Connected to {address}")

            CONNECT_GEN += 1
            my_gen = CONNECT_GEN
            SERVICES_READY = False

            def is_current():
                return (connected_client is client) and (my_gen == CONNECT_GEN)

            def _on_disconnect(_):
                if is_current():
                    log_message(f"Disconnected from {address}")
                    # Invalidate services so UI actions are ignored
                    global SERVICES_READY  # type: ignore  # we're in outer async def scope
                    SERVICES_READY = False

            try:
                client.set_disconnected_callback(_on_disconnect)
            except Exception:
                pass

            # Resolve services
            services = await client.get_services()
            for service in services:
                svc_uuid = str(service.uuid).lower()
                log_message(f"Service: {service.uuid}")
                for char in service.characteristics:
                    cu = str(char.uuid).lower()
                    props = list(char.properties)
                    log_message(f"  Characteristic: {char.uuid} (Properties: {props})")
                    discovered_chars.append((str(char.uuid), props))
                    char_service_map[cu] = svc_uuid
                    if (("notify" in props) or ("indicate" in props)) and \
                       svc_uuid not in ("00001800-0000-1000-8000-00805f9b34fb", "00001801-0000-1000-8000-00805f9b34fb") and \
                       cu != "00002a05-0000-1000-8000-00805f9b34fb":
                        await _safe_notify(client, char.uuid)

            await ensure_vendor_notifies(client)
            await dump_descriptors(client)

            for i, (uuid, props) in enumerate(discovered_chars):
                log_message(f"[{i}] {uuid} {props}")

            SERVICES_READY = True

            if AUTO_SCAN_READS:
                log_message("Auto-scan readable characteristics...")
                run_coro(_scan_all_readable(client))

            run_coro(_keepalive(client))

            while not stop_event.is_set() and await _is_live(client):
                await asyncio.sleep(0.1)
            return

        except Exception as e:
            log_message(f"Error: {e}")
            if attempt < retries - 1:
                log_message(f"Retrying connection ({attempt + 1}/{retries})...")
            else:
                log_message("Failed to connect after multiple attempts.")
        finally:
            if client:
                try:
                    if await _is_live(client):
                        await client.disconnect()
                except Exception as e2:
                    log_message(f"Disconnect error: {e2}")
            connected_client = None
            connected_device = None
            SERVICES_READY = False

# -------------------- Actions --------------------
def _ready_or_log():
    if not connected_client or not SERVICES_READY:
        log_message("Skip: not connected / services not ready.")
        return False
    return True

def notification_handler(sender, data):
    try:
        u = str(sender)
    except Exception:
        u = f"{sender}"
    global LAST_NOTIFY_UUID, LAST_NOTIFY_T
    LAST_NOTIFY_UUID, LAST_NOTIFY_T = u.lower(), monotonic()
    hexp = data.hex() if isinstance(data, (bytes, bytearray)) else str(data)
    log_message(f"Notification from {u}: {hexp}")

def _props_for(uuid_str: str):
    u = uuid_str.lower()
    for uu, props in discovered_chars:
        if uu.lower() == u:
            return set(props)
    return set()

def _resolve_char(sel):
    if isinstance(sel, int) and 0 <= sel < len(discovered_chars):
        return discovered_chars[sel][0]
    return str(sel).strip()

def read_char(sel):
    if not _ready_or_log(): return
    uuid = _resolve_char(sel)
    props = _props_for(uuid)
    if "read" not in props:
        log_message(f"READ not allowed on {uuid}. Props={sorted(list(props))}")
        return
    async def _r():
        try:
            data = await connected_client.read_gatt_char(uuid)  # type: ignore
            log_message(f"READ {uuid}: {data.hex()} | {data!r}")
        except Exception as e:
            log_message(f"READ error {uuid}: {e}")
            if "was not found" in str(e):
                run_coro(safe_refresh(connected_client))  # type: ignore
    run_coro(_r())

def write_char(sel, payload: bytes, response=False):
    if not _ready_or_log(): return
    uuid = _resolve_char(sel)
    props = _props_for(uuid)
    if "write" not in props and "write-without-response" not in props:
        log_message(f"WRITE not allowed on {uuid}. Props={sorted(list(props))}")
        return
    response = ("write-without-response" not in props) if response is False else response
    async def _w():
        try:
            await connected_client.write_gatt_char(uuid, payload, response=response)  # type: ignore
            log_message(f"WROTE {uuid}: {payload.hex()} (resp={response})")
        except Exception as e:
            log_message(f"WRITE error {uuid}: {e}")
            if "was not found" in str(e):
                run_coro(safe_refresh(connected_client))  # type: ignore
    run_coro(_w())

def _safe_hex(b: bytes, limit=READ_MAX_BYTES_LOG):
    if b is None:
        return ""
    if len(b) > limit:
        return f"{b[:limit].hex()}...(+{len(b)-limit}B)"
    return b.hex()

async def _scan_all_readable(c=None):
    c = c or connected_client
    if not c:
        log_message("Not connected.")
        return
    try:
        async def _live():
            try:
                return await c.is_connected()
            except Exception:
                return True
        for uuid, props in list(discovered_chars):
            if "read" not in set(props):
                continue
            if not await _live():
                log_message("Auto-scan aborted: device disconnected.")
                break
            try:
                data = await c.read_gatt_char(uuid)
                log_message(f"READ {uuid}: {_safe_hex(data)}")
            except Exception as e:
                log_message(f"READ error {uuid}: {e}")
                if "was not found" in str(e):
                    run_coro(safe_refresh(c))
            await asyncio.sleep(READ_THROTTLE_MS / 1000.0)
    except Exception as e:
        log_message(f"Auto-scan error: {e}")

def read_all_readable(client=None):
    if not _ready_or_log(): return
    run_coro(_scan_all_readable(client))

def set_notify(sel, enable=True):
    if not _ready_or_log(): return
    uuid = _resolve_char(sel)
    async def _n():
        try:
            if enable:
                await connected_client.start_notify(uuid, notification_handler)  # type: ignore
                log_message(f"NOTIFY ON {uuid}")
            else:
                await connected_client.stop_notify(uuid)  # type: ignore
                log_message(f"NOTIFY OFF {uuid}")
        except Exception as e:
            log_message(f"NOTIFY error {uuid}: {e}")
            if "was not found" in str(e):
                run_coro(safe_refresh(connected_client))  # type: ignore
    run_coro(_n())

VENDOR_SVC = "da2b84f1-6279-48de-bdc0-afbea0226079"
PROBE_PAYLOADS = [
    b"\x01", b"\x02", b"\x03", b"\x00", b"\x01\x00", b"\x00\x01",
    b"\x55", b"\xaa", b"\xa0", b"\xff", b"R", b"READ", b"MEAS", b"START"
]
PROBE_DELAY_MS = 180

def _vendor_write_chars():
    out = []
    for uuid, props in discovered_chars:
        u = uuid.lower()
        svc = char_service_map.get(u, "").lower()
        p = set(props)
        if svc == VENDOR_SVC and (("write" in p) or ("write-without-response" in p)):
            out.append((uuid, p))
    return out

def probe_for_measure():
    if not _ready_or_log(): return
    targets = _vendor_write_chars()
    if not targets:
        log_message("No vendor write characteristics found.")
        return
    log_message(f"Probing {len(targets)} characteristic(s) with {len(PROBE_PAYLOADS)} payload(s)...")
    async def _probe():
        for uuid, props in targets:
            for payload in PROBE_PAYLOADS:
                try:
                    use_resp = ("write-without-response" not in props)
                    await connected_client.write_gatt_char(uuid, payload, response=use_resp)  # type: ignore
                    log_message(f"PROBE write {uuid}: {payload!r} (resp={use_resp})")
                    if "read" in props:
                        try:
                            rb = await connected_client.read_gatt_char(uuid)  # type: ignore
                            log_message(f"PROBE readback {uuid}: {rb.hex()}")
                        except Exception as e:
                            log_message(f"Readback error {uuid}: {e}")
                except Exception as e:
                    log_message(f"Probe error {uuid}: {e}")
                    if "was not found" in str(e):
                        run_coro(safe_refresh(connected_client))  # type: ignore
                await asyncio.sleep(PROBE_DELAY_MS/1000.0)
    run_coro(_probe())

def brute_force_triggers():
    if not _ready_or_log(): return
    async def _run():
        try:
            try:
                await connected_client.write_gatt_char(MEASURE_PRIME_UUID, b"\x01", response=True)  # type: ignore
                log_message("Prime: wrote 01 to a87988b9")
            except Exception as e:
                log_message(f"Prime write failed: {e}")
            await asyncio.sleep(0.2)
            for ret_uuid in (MEASURE_RET_UUID, "18cda784-4bd3-4370-85bb-bfed91ec86af"):
                try:
                    await connected_client.start_notify(ret_uuid, notification_handler)  # type: ignore
                except Exception:
                    pass
            def notified_recently(window=0.8):
                return (monotonic() - LAST_NOTIFY_T) < window
            log_message("BF: 1-byte 0x00..0xFF on bf03260c")
            for x in range(256):
                try:
                    await connected_client.write_gatt_char(MEASURE_TRIG_UUID, bytes([x]), response=True)  # type: ignore
                except Exception as e:
                    log_message(f"BF write {x:02X} err: {e}")
                await asyncio.sleep(0.08)
                if notified_recently():
                    log_message(f"BF hit after 1-byte {x:02X}")
                    return
            for seed in (0x01, 0x02, 0x10):
                log_message(f"BF: 2-byte [{seed:02X},x] on bf03260c")
                for x in range(256):
                    p = bytes([seed, x])
                    try:
                        await connected_client.write_gatt_char(MEASURE_TRIG_UUID, p, response=True)  # type: ignore
                    except Exception as e:
                        log_message(f"BF write {p.hex()} err: {e}")
                    await asyncio.sleep(0.08)
                    if notified_recently():
                        log_message(f"BF hit after 2-byte {p.hex()}")
                        return
            log_message("BF done. No trigger found.")
        except Exception as e:
            log_message(f"BF failed: {e}")
    run_coro(_run())

def brute_force_alt_on_0a19():
    if not _ready_or_log(): return
    async def _run():
        try:
            try:
                await connected_client.write_gatt_char(MEASURE_PRIME_UUID, b"\x01", response=True)  # type: ignore
                log_message("Prime: wrote 01 to a87988b9")
            except Exception as e:
                log_message(f"Prime write failed: {e}")
            await asyncio.sleep(0.2)
            for ret_uuid in (MEASURE_RET_UUID, "18cda784-4bd3-4370-85bb-bfed91ec86af"):
                try:
                    await connected_client.start_notify(ret_uuid, notification_handler)  # type: ignore
                except Exception:
                    pass
            def notified_recently(window=0.8):
                return (monotonic() - LAST_NOTIFY_T) < window
            log_message("BF ALT: 1-byte on 0a1934f5")
            for x in range(256):
                p = bytes([x])
                try:
                    await connected_client.write_gatt_char(MEASURE_ALT_UUID, p, response=False)  # type: ignore
                except Exception as e:
                    log_message(f"BF ALT write {x:02X} err: {e}")
                await asyncio.sleep(0.08)
                try:
                    rb = await connected_client.read_gatt_char(MEASURE_ALT_UUID)  # type: ignore
                    log_message(f"ALT readback 0a1934f5: {rb.hex()}")
                except Exception:
                    pass
                if notified_recently():
                    log_message(f"BF ALT hit after 1-byte {x:02X}")
                    return
            for seed in (0x01, 0x02, 0x10, 0x20):
                log_message(f"BF ALT: 2-byte [{seed:02X},x] on 0a1934f5")
                for x in range(256):
                    p = bytes([seed, x])
                    try:
                        await connected_client.write_gatt_char(MEASURE_ALT_UUID, p, response=False)  # type: ignore
                    except Exception as e:
                        log_message(f"BF ALT write {p.hex()} err: {e}")
                    await asyncio.sleep(0.08)
                    if notified_recently():
                        log_message(f"BF ALT hit after 2-byte {p.hex()}")
                        return
            log_message("BF ALT done. No trigger found.")
        except Exception as e:
            log_message(f"BF ALT failed: {e}")
    run_coro(_run())

def attempt_measure():
    if not _ready_or_log(): return
    async def _run():
        try:
            try:
                rb = await connected_client.read_gatt_char(MEASURE_PRIME_UUID)  # type: ignore
                log_message(f"Prime readback a87988b9: {rb.hex()}")
            except Exception as e:
                log_message(f"Prime readback failed: {e}")
            try:
                rb = await connected_client.read_gatt_char("99564a02-dc01-4d3c-b04e-3bb1ef0571b2")  # type: ignore
                log_message(f"Info 99564a02: {rb.hex()}")
            except Exception as e:
                log_message(f"Info read failed: {e}")
            await asyncio.sleep(0.12)
            for ret_uuid in (MEASURE_RET_UUID, "18cda784-4bd3-4370-85bb-bfed91ec86af"):
                try:
                    await connected_client.start_notify(ret_uuid, notification_handler)  # type: ignore
                except Exception:
                    pass
            def frames():
                base = [b"\x01", b"\x02", b"\x03", b"\x10", b"\x20", b"\x30"]
                cmds = [0x01, 0x02, 0x10, 0x20]
                for c in cmds:
                    hdr = b"\xAA\x55"; plen = b"\x00"; body = bytes([c])
                    crc = bytes([(sum(hdr+plen+body) & 0xFF) ^ 0xFF])
                    yield hdr + plen + body + crc
                    hdr2 = b"\x55\xAA"
                    crc2 = bytes([(sum(hdr2+plen+body) & 0xFF) ^ 0xFF])
                    yield hdr2 + plen + body + crc2
                for b1 in base:
                    yield b1
                for t in (b"M", b"G", b"C", b"R", b"MEAS", b"READ", b"START"):
                    yield t
            for p in frames():
                try:
                    await connected_client.write_gatt_char(MEASURE_TRIG_UUID, p, response=True)  # type: ignore
                    log_message(f"Trigger write bf03260c: {p!r}")
                except Exception as e:
                    log_message(f"Trigger error bf03260c {p!r}: {e}")
                    if "was not found" in str(e):
                        run_coro(safe_refresh(connected_client))  # type: ignore
                waited = 0.0
                step = 0.1
                while waited < 1.5:
                    await asyncio.sleep(step)
                    waited += step
            alt_cmds = [b"\x01", b"\x02", b"\x10", b"M", b"MEAS", b"R"]
            for p in alt_cmds:
                try:
                    await connected_client.write_gatt_char(MEASURE_ALT_UUID, p, response=False)  # type: ignore
                    log_message(f"Alt write 0a1934f5: {p!r}")
                    try:
                        rb = await connected_client.read_gatt_char(MEASURE_ALT_UUID)  # type: ignore
                        log_message(f"Alt readback 0a1934f5: {rb.hex()}")
                    except Exception as e:
                        log_message(f"Alt readback error: {e}")
                except Exception as e:
                    log_message(f"Alt write error {p!r}: {e}")
                await asyncio.sleep(0.12)
            log_message("Measure attempt sequence done.")
        except Exception as e:
            log_message(f"Measure attempt failed: {e}")
    run_coro(_run())

# -------------------- UI handlers --------------------
def start_scan():
    global selected_device_index
    selected_device_index = -1
    run_coro(scan_devices())

def start_connection():
    global selected_device_index, scanned_devices, connected_client
    if selected_device_index == -1:
        return
    if connected_client is not None:
        log_message("Already connected; ignoring new connect request.")
        return
    selected_device = scanned_devices[selected_device_index]
    address = selected_device.split("(")[-1].strip(")")
    run_coro(connect_to_device(address))

def handle_list_click(mouse_pos):
    global selected_device_index
    if scanning or not scanned_devices:
        return
    if list_rect.collidepoint(mouse_pos):
        relative_y = mouse_pos[1] - LIST_Y - 10
        index = relative_y // 30
        if 0 <= index < len(scanned_devices):
            selected_device_index = index
            pyperclip.copy(scanned_devices[selected_device_index])
            print(f"Copied to clipboard: {scanned_devices[selected_device_index]}")

def handle_scroll(event):
    global log_scroll_offset
    if log_rect.collidepoint(event.pos):
        if event.button == 4:
            log_scroll_offset = max(0, log_scroll_offset - 20)
        elif event.button == 5:
            log_scroll_offset += 20

def draw_wrapped_text(surface, text, x, y, max_w, font, color):
    words = text.split()
    line = ""
    yy = y
    for w in words:
        test = (line + " " + w).strip()
        if font.size(test)[0] <= max_w:
            line = test
        else:
            surface.blit(font.render(line, True, color), (x, yy))
            yy += font.get_height() + 2
            line = w
    if line:
        surface.blit(font.render(line, True, color), (x, yy))

def draw_interface():
    screen.fill(WHITE)
    button_color = GRAY if scanning else BLUE
    pygame.draw.rect(screen, button_color, button_rect, border_radius=6)
    button_text = FONT.render("Scanning..." if scanning else "Scan for Devices", True, WHITE)
    screen.blit(button_text, (button_rect.x + 20, button_rect.y + 8))

    pygame.draw.rect(screen, BLACK, list_rect, 2)
    if scanning or not scanned_devices:
        pygame.draw.rect(screen, LIGHT_GRAY, list_rect.inflate(-4, -4))
    prev_clip = screen.get_clip()
    screen.set_clip(list_rect)

    y_offset = LIST_Y + 10
    if not scanned_devices and not scanning:
        no_device_text = SMALL_FONT.render("No devices found. Click 'Scan for Devices' to start.", True, RED)
        screen.blit(no_device_text, (LIST_X + 10, y_offset))
    else:
        for index, device in enumerate(scanned_devices):
            if index == selected_device_index:
                pygame.draw.rect(screen, LIGHT_GRAY, (LIST_X + 5, y_offset - 5, LIST_WIDTH - 10, 30))
            device_text = SMALL_FONT.render(device, True, BLACK)
            if LIST_Y <= y_offset <= LIST_Y + LIST_HEIGHT - 20:
                screen.blit(device_text, (LIST_X + 10, y_offset))
            y_offset += 30
    screen.set_clip(prev_clip)

    pygame.draw.rect(screen, BLACK, log_rect, 2)
    prev_clip = screen.get_clip()
    screen.set_clip(log_rect)
    traffic_y_offset = LOG_Y + 10 - log_scroll_offset
    for log in traffic_log:
        log_text = SMALL_FONT.render(log, True, BLACK)
        if LOG_Y <= traffic_y_offset <= LOG_Y + LOG_HEIGHT - 20:
            screen.blit(log_text, (LOG_X + 10, traffic_y_offset))
        traffic_y_offset += 20
    screen.set_clip(prev_clip)

    help_y = SCREEN_HEIGHT - (SMALL_FONT.get_height()*2) - 10
    pygame.draw.line(screen, LIGHT_GRAY, (50, help_y - 6), (SCREEN_WIDTH - 50, help_y - 6), 1)
    draw_wrapped_text(screen, HELP_TEXT, 50, help_y, SCREEN_WIDTH - 100, SMALL_FONT, BLACK)
    pygame.display.flip()

# -------------------- Main loop --------------------
def main():
    global selected_char_index  # fix UnboundLocalError when assigning below
    running = True
    clock = pygame.time.Clock()

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                stop_event.set()
                running = False

            elif event.type == pygame.KEYDOWN:
                if pygame.K_0 <= event.key <= pygame.K_9:
                    idx = event.key - pygame.K_0
                    if 0 <= idx < len(discovered_chars):
                        selected_char_index = idx
                        log_message(f"Selected char [{idx}] {discovered_chars[idx][0]}")
                elif event.key == pygame.K_r and selected_char_index != -1:
                    read_char(selected_char_index)
                elif event.key == pygame.K_n and selected_char_index != -1:
                    set_notify(selected_char_index, True)
                elif event.key == pygame.K_u and selected_char_index != -1:
                    set_notify(selected_char_index, False)
                elif event.key == pygame.K_b and (event.mod & pygame.KMOD_SHIFT):
                    brute_force_alt_on_0a19()
                elif event.key == pygame.K_b:
                    brute_force_triggers()
                elif event.key == pygame.K_p:
                    probe_for_measure()
                elif event.key == pygame.K_a:
                    read_all_readable()
                elif event.key == pygame.K_m:
                    attempt_measure()
                elif event.key == pygame.K_d:
                    if connected_client:
                        run_coro(dump_descriptors(connected_client))
                elif event.key == pygame.K_w and selected_char_index != -1:
                    clip = pyperclip.paste() or ""
                    try:
                        payload = bytes.fromhex(clip.replace("0x", "").replace(",", " ").strip())
                    except ValueError:
                        payload = clip.encode("utf-8")
                    props = set(dict(discovered_chars).get(_resolve_char(selected_char_index), []))
                    write_char(selected_char_index, payload, response=("write-without-response" not in props))

            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button in (4, 5):
                    handle_scroll(event)
                elif button_rect.collidepoint(event.pos):
                    start_scan()
                else:
                    handle_list_click(event.pos)
                    if selected_device_index != -1:
                        start_connection()

        draw_interface()
        clock.tick(30)

    pygame.quit()
    # Optional: stop the global loop if you want a clean exit
    try:
        _LOOP.call_soon_threadsafe(_LOOP.stop)
    except Exception:
        pass

if __name__ == "__main__":
    main()
