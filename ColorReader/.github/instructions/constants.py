# constants.py

# ---- App/UI ----
APP_TITLE = "ColorReader BLE Scanner"
SCREEN_WIDTH = 1000
SCREEN_HEIGHT = 680

# Colors (RGB)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
BLUE = (0, 122, 255)
GRAY = (200, 200, 200)
RED = (255, 0, 0)
LIGHT_GRAY = (220, 220, 220)
DARK_GRAY = (70, 70, 70)

HELP_TEXT = (
    "Keyboard/Buttons:\n"
    "• Scan: Find nearby BLE devices\n"
    "• Connect: Connect to selected device in the device list\n"
    "• Read: Read selected characteristic\n"
    "• Write: Write bytes from clipboard (hex or text) to selected characteristic\n"
    "• Notify On/Off: Subscribe/Unsubscribe notifications for selected char\n"
    "• Read All: Auto-scan all readable characteristics\n"
    "• Probe: Probe vendor characteristics with common payloads\n"
    "• Measure: Attempt a measurement sequence\n"
    "• Brute: Brute force triggers on bf03260c\n"
    "• BruteAlt: Brute force alt path on 0a1934f5\n"
    "\nSelection:\n"
    "• Click a device (left list) to select it, then click Connect.\n"
    "• Click a characteristic (right list) to select it.\n"
    "\nTips:\n"
    "• Clipboard hex like '01 02 aa 55' is accepted for Write; otherwise raw text.\n"
    "• Log area scrolls with mouse wheel.\n"
)

# ---- BLE UUIDs & behavior ----
MEASURE_PRIME_UUID = "a87988b9-694c-479c-900e-95dfa6c00a24"   # write+read
MEASURE_TRIG_UUID  = "bf03260c-7205-4c25-af43-93b1c299d159"   # write-only
MEASURE_RET_UUID   = "fdd6b4d3-046d-4330-bdec-1fd0c90cb43b"   # notify/indicate
MEASURE_ALT_UUID   = "0a1934f5-24b8-4f13-9842-37bb167c6aff"   # write/wr-no-rsp/read (echo/status)
FORCE_SUBSCRIBE_UUIDS = [MEASURE_RET_UUID, "18cda784-4bd3-4370-85bb-bfed91ec86af"]

VENDOR_SVC = "da2b84f1-6279-48de-bdc0-afbea0226079"

AUTO_SCAN_READS = True
READ_THROTTLE_MS = 80
READ_MAX_BYTES_LOG = 64
MEASURE_SETTLE_MS = 120
MEASURE_NOTIFY_WINDOW_MS = 1500

PROBE_PAYLOADS = [
    b"\x01", b"\x02", b"\x03", b"\x00", b"\x01\x00", b"\x00\x01",
    b"\x55", b"\xaa", b"\xa0", b"\xff", b"R", b"READ", b"MEAS", b"START"
]
PROBE_DELAY_MS = 180
