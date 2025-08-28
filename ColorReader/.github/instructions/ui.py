# ui.py
import math
import pygame
import pyperclip
from typing import Callable, List, Tuple, Optional
from constants import (
    APP_TITLE,
    WHITE, BLACK, BLUE, GRAY, RED, LIGHT_GRAY, DARK_GRAY, HELP_TEXT
)

MIN_BTN_W = 96
BTN_H = 34
BTN_GAP = 10
PADDING = 16
MAX_TOOLBAR_ROWS = 2

class UIButton:
    def __init__(self, label: str, onclick: Callable[[], None]):
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.label = label
        self.onclick = onclick
        self.enabled = True

    def draw(self, screen, font):
        color = BLUE if self.enabled else GRAY
        pygame.draw.rect(screen, color, self.rect, border_radius=6)
        text = font.render(self.label, True, WHITE)
        screen.blit(text, (self.rect.x + 10, self.rect.y + (self.rect.h - text.get_height()) // 2))

    def handle(self, event):
        if not self.enabled:
            return
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.onclick()


class Toolbar:
    """Responsive toolbar that lays out buttons in up to 2 rows, resizing as needed."""
    def __init__(self, x: int, y: int, width: int, button_height: int = BTN_H, gap: int = BTN_GAP):
        self.x, self.y, self.width = x, y, width
        self.h = button_height
        self.gap = gap
        self.buttons: List[UIButton] = []
        self.rect = pygame.Rect(x, y, width, button_height)  # height will be recomputed

    def set_width(self, width: int):
        self.width = max(200, width)
        self.layout()

    def add_button(self, btn: UIButton):
        self.buttons.append(btn)
        self.layout()

    def layout(self):
        n = len(self.buttons)
        if n == 0:
            self.rect.height = self.h
            return

        # Decide column count so that rows <= MAX_TOOLBAR_ROWS.
        rows = min(MAX_TOOLBAR_ROWS, n)
        cols = math.ceil(n / rows)

        # Compute button width to fit cols across available width.
        total_gap = (cols - 1) * self.gap
        avail = max(150, self.width - 2 * PADDING - total_gap)
        btn_w = max(MIN_BTN_W, avail // cols)

        # If still too wide, increase rows (up to MAX) and recompute.
        while rows < MAX_TOOLBAR_ROWS and (btn_w * cols + total_gap) > self.width - 2 * PADDING:
            rows += 1
            cols = math.ceil(n / rows)
            total_gap = (cols - 1) * self.gap
            avail = max(150, self.width - 2 * PADDING - total_gap)
            btn_w = max(MIN_BTN_W, avail // cols)

        # Final height
        self.rect.height = rows * self.h + (rows - 1) * self.gap

        # Place buttons row by row
        x0, y0 = self.x + PADDING, self.y + 0
        for i, b in enumerate(self.buttons):
            r = i // cols
            c = i % cols
            bx = x0 + c * (btn_w + self.gap)
            by = y0 + r * (self.h + self.gap)
            b.rect = pygame.Rect(bx, by, btn_w, self.h)

    def draw(self, screen, font):
        for b in self.buttons:
            b.draw(screen, font)

    def handle(self, event):
        for b in self.buttons:
            b.handle(event)


class UI:
    def __init__(self, log_fn: Callable[[str], None]):
        pygame.init()
        pygame.font.init()
        self.font = pygame.font.SysFont("Arial", 20)
        self.sfont = pygame.font.SysFont("Arial", 16)

        # RESIZABLE window
        self.size = (1000, 680)
        self.screen = pygame.display.set_mode(self.size, pygame.RESIZABLE)
        pygame.display.set_caption(APP_TITLE)

        self.log_fn = log_fn

        # panels
        self.devices: List[str] = []
        self.selected_device_idx: int = -1

        self.chars: List[Tuple[str, List[str]]] = []
        self.selected_char_idx: int = -1

        self.logs: List[str] = []
        self.log_offset = 0

        self.show_help = False

        # toolbar (responsive)
        self.toolbar = Toolbar(x=0, y=8, width=self.size[0])

        # callbacks (wired later)
        self._scan_cb = None
        self._connect_cb = None
        self._read_cb = None
        self._write_cb = None
        self._notify_on_cb = None
        self._notify_off_cb = None
        self._read_all_cb = None
        self._probe_cb = None
        self._measure_cb = None
        self._brute_cb = None
        self._brute_alt_cb = None
        self._dump_cccd_cb = None

        # add buttons
        def add(label, cb):
            self.toolbar.add_button(UIButton(label, cb))

        add("Scan",          lambda: self._scan_cb and self._scan_cb())
        add("Connect",       lambda: self._connect_cb and self._connect_cb())
        add("Read",          lambda: self._read_cb and self._read_cb())
        add("Write",         lambda: self._write_cb and self._write_from_clipboard())
        add("Notify On",     lambda: self._notify_on_cb and self._notify_on_cb())
        add("Notify Off",    lambda: self._notify_off_cb and self._notify_off_cb())
        add("Read All",      lambda: self._read_all_cb and self._read_all_cb())
        add("Probe",         lambda: self._probe_cb and self._probe_cb())
        add("Measure",       lambda: self._measure_cb and self._measure_cb())
        add("Brute",         lambda: self._brute_cb and self._brute_cb())
        add("BruteAlt",      lambda: self._brute_alt_cb and self._brute_alt_cb())
        add("DumpCCCD",      lambda: self._dump_cccd_cb and self._dump_cccd_cb())
        add("Help",          self._toggle_help)

        # layout rects (computed in _relayout)
        self.left_list = pygame.Rect(0, 0, 0, 0)
        self.right_list = pygame.Rect(0, 0, 0, 0)
        self.log_rect = pygame.Rect(0, 0, 0, 0)
        self._relayout()

    # ---------- wiring callbacks ----------

    def bind(
        self,
        scan_cb, connect_cb, read_cb, write_cb, notify_on_cb, notify_off_cb,
        read_all_cb, probe_cb, measure_cb, brute_cb, brute_alt_cb, dump_cccd_cb
    ):
        self._scan_cb = scan_cb
        self._connect_cb = connect_cb
        self._read_cb = read_cb
        self._write_cb = write_cb
        self._notify_on_cb = notify_on_cb
        self._notify_off_cb = notify_off_cb
        self._read_all_cb = read_all_cb
        self._probe_cb = probe_cb
        self._measure_cb = measure_cb
        self._brute_cb = brute_cb
        self._brute_alt_cb = brute_alt_cb
        self._dump_cccd_cb = dump_cccd_cb

    # ---------- state setters ----------

    def set_devices(self, items: List[str]):
        self.devices = items
        self.selected_device_idx = -1

    def set_chars(self, items: List[Tuple[str, List[str]]]):
        self.chars = items
        self.selected_char_idx = -1

    def log(self, msg: str):
        self.logs.append(msg)
        print(msg)

    # ---------- helpers ----------

    def _toggle_help(self):
        self.show_help = not self.show_help

    def _write_from_clipboard(self):
        if not self._write_cb:
            return
        clip = pyperclip.paste() or ""
        cleaned = clip.replace("0x", "").replace(",", " ").replace("\n", " ").strip()
        try:
            payload = bytes.fromhex(" ".join(cleaned.split()))
        except ValueError:
            payload = clip.encode("utf-8")
        self._write_cb(payload)

    def _relayout(self):
        w, h = self.size
        # resize toolbar and compute its height
        self.toolbar.set_width(w)
        tb_h = self.toolbar.rect.height + 8  # small bottom spacing

        # top lists side-by-side with padding
        top_y = tb_h + 10
        top_h = max(220, h // 3)
        col_gap = PADDING
        side_w = (w - (PADDING * 2) - col_gap) // 2

        self.left_list = pygame.Rect(PADDING, top_y, side_w, top_h)
        self.right_list = pygame.Rect(PADDING + side_w + col_gap, top_y, side_w, top_h)

        # log takes the rest
        log_y = self.right_list.bottom + 16
        self.log_rect = pygame.Rect(PADDING, log_y, w - 2 * PADDING, h - log_y - PADDING)

    # ---------- input / events ----------

    def handle_event(self, event):
        if event.type == pygame.VIDEORESIZE:
            # window resize → relayout
            self.size = (max(720, event.w), max(520, event.h))
            self.screen = pygame.display.set_mode(self.size, pygame.RESIZABLE)
            self._relayout()
            return

        if event.type == pygame.KEYDOWN:
            # Close help with ESC
            if event.key == pygame.K_ESCAPE and self.show_help:
                self.show_help = False

        if event.type == pygame.MOUSEBUTTONDOWN:
            # If help open, click anywhere to close
            if self.show_help:
                self.show_help = False
                return

            # log scroll
            if self.log_rect.collidepoint(event.pos):
                if event.button == 4:
                    self.log_offset = max(0, self.log_offset - 20)
                elif event.button == 5:
                    self.log_offset += 20

            # device select
            if self.left_list.collidepoint(event.pos):
                rel_y = event.pos[1] - (self.left_list.y + 10)
                idx = rel_y // 22
                if 0 <= idx < len(self.devices):
                    self.selected_device_idx = idx
                    try:
                        pyperclip.copy(self.devices[idx])
                    except Exception:
                        pass

            # char select
            if self.right_list.collidepoint(event.pos):
                rel_y = event.pos[1] - (self.right_list.y + 10)
                idx = rel_y // 22
                if 0 <= idx < len(self.chars):
                    self.selected_char_idx = idx

            # toolbar buttons
            self.toolbar.handle(event)

    # ---------- drawing ----------

    def draw_list(self, rect: pygame.Rect, rows: List[str], selected_idx: int):
        pygame.draw.rect(self.screen, BLACK, rect, 2)
        prev = self.screen.get_clip()
        self.screen.set_clip(rect)
        y = rect.y + 10
        for i, t in enumerate(rows):
            if i == selected_idx:
                pygame.draw.rect(self.screen, LIGHT_GRAY, (rect.x + 4, y - 3, rect.w - 8, 22))
            # clip text horizontally
            text_surface = self.sfont.render(t, True, BLACK)
            max_w = rect.w - 16
            if text_surface.get_width() > max_w:
                # ellipsis
                txt = t
                while self.sfont.size(txt + "…")[0] > max_w and len(txt) > 3:
                    txt = txt[:-1]
                text_surface = self.sfont.render(txt + "…", True, BLACK)
            if rect.y <= y <= rect.y + rect.h - 20:
                self.screen.blit(text_surface, (rect.x + 8, y))
            y += 22
        self.screen.set_clip(prev)

    def draw_logs(self):
        pygame.draw.rect(self.screen, BLACK, self.log_rect, 2)
        prev = self.screen.get_clip()
        self.screen.set_clip(self.log_rect)
        y = self.log_rect.y + 10 - self.log_offset
        for line in self.logs:
            label = self.sfont.render(line, True, BLACK)
            if self.log_rect.y <= y <= self.log_rect.y + self.log_rect.h - 20:
                self.screen.blit(label, (self.log_rect.x + 8, y))
            y += 18
        self.screen.set_clip(prev)

    def draw_help_modal(self):
        overlay = pygame.Surface(self.size, pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 140))
        self.screen.blit(overlay, (0, 0))
        box_w, box_h = int(self.size[0] * 0.72), int(self.size[1] * 0.72)
        box = pygame.Rect((self.size[0] - box_w)//2, (self.size[1] - box_h)//2, box_w, box_h)
        pygame.draw.rect(self.screen, WHITE, box, border_radius=10)
        pygame.draw.rect(self.screen, DARK_GRAY, box, 2, border_radius=10)

        title = self.font.render("Help", True, BLACK)
        self.screen.blit(title, (box.x + 12, box.y + 10))

        # wrap text
        x, y = box.x + 12, box.y + 46
        max_w = box.w - 24
        for para in HELP_TEXT.split("\n"):
            if not para:
                y += self.sfont.get_height() + 6
                continue
            line = ""
            for w in para.split():
                test = (line + " " + w).strip()
                if self.sfont.size(test)[0] <= max_w:
                    line = test
                else:
                    self.screen.blit(self.sfont.render(line, True, BLACK), (x, y))
                    y += self.sfont.get_height() + 4
                    line = w
            if line:
                self.screen.blit(self.sfont.render(line, True, BLACK), (x, y))
                y += self.sfont.get_height() + 6

        # hint to close
        hint = self.sfont.render("(Click anywhere or press ESC to close)", True, DARK_GRAY)
        self.screen.blit(hint, (box.x + 12, box.bottom - hint.get_height() - 10))

    def draw(self):
        self.screen.fill(WHITE)

        # toolbar
        self.toolbar.draw(self.screen, self.sfont)

        # labels
        self.screen.blit(self.font.render("Devices", True, BLACK), (self.left_list.x, self.left_list.y - 26))
        self.screen.blit(self.font.render("Characteristics", True, BLACK), (self.right_list.x, self.right_list.y - 26))
        self.screen.blit(self.font.render("Log", True, BLACK), (self.log_rect.x, self.log_rect.y - 26))

        # lists
        self.draw_list(self.left_list, self.devices, self.selected_device_idx)
        char_rows = [f"{i:02d} {u}   {props}" for i, (u, props) in enumerate(self.chars)]
        self.draw_list(self.right_list, char_rows, self.selected_char_idx)

        # logs
        self.draw_logs()

        if self.show_help:
            self.draw_help_modal()

        pygame.display.flip()
