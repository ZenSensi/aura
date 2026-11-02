"""
dock.py - The Aura dock: a small floating pill at the top-centre of the screen
(or another corner or edge: Settings > Dock).

When the main window is minimised, the dock appears (like a "Dynamic Island").
The dock's shortcut (hotkey.py) opens it from any app. With autohide on, it only
appears for the shortcut and hides again AUTOHIDE seconds after you stop using it.
Click it to expand, type a task, and the agents' results appear right in the dock.
"Open Aura" brings the main window back (and the dock hides again).

How it works:
  - It is a second pywebview window: frameless, always on top, hidden at start.
  - The window always has the same size. Windows API calls (ctypes) cut it
    down to the shape you see (pill or card), keep it on top and keep it out
    of the taskbar. Growing / shrinking is animated by the page (dock.css);
    the cut-out just makes room for it (see set_shape).
  - "Glass" look (like macOS): we keep taking pictures of the screen behind
    the dock, blur them, and use them as the dock's background (see _glass),
    so it shows whatever is really behind it, live: ~15 times a second while
    the background moves (a video, a game), twice a second while it is still. While taking the picture the
    dock hides itself from screen captures (only for that moment), so it isn't in
    its own picture. Windows' own blur effects ignore the pill shape, so they
    can't be used here.
  - Now Playing (like the iPhone's Dynamic Island): while a song or video plays,
    the pill shows its cover, title and progress, with play / pause / next
    (nowplaying.py reads Windows' media sessions).
  - Its page (static/dock.html) uses the same Api as the main window.
"""

import base64
import ctypes
import ctypes.wintypes
import io
import os
import sys
import threading
import time

import json

import webview

import config
from PIL import Image, ImageEnhance, ImageFilter

import hotkey
import voice
from nowplaying import NowPlaying
import notch
from notch import Clipboard, Devices, Hud

WIDTH, HEIGHT = 660, 640   # the window never changes size: only its visible shape does.
                           # (pywebview makes a frameless window ~16 x 39 px smaller than
                           # asked; the page measures its real size, so a little spare is fine)
PILL = (300, 44)           # pill size (width, height), including the 2px ring (see dock.css)
NOTCH = (364, 50)          # the notch is a little bigger (dock.css / dock.js)
NOTCH_EAR = 10             # the notch's curved top shoulders (html::before/::after in dock.css)
DND_MARK = os.path.join(config.BASE_DIR, "focus_dnd.mark")  # there while the focus timer has Do Not Disturb on


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass
CARD_RADIUS = 22           # corner radius of the shape (the page draws 20 + the 2px ring)
ANIMATION = 0.45           # seconds; same as the grow/shrink animation in dock.css
MARGIN = 8                 # gap from the edges of the screen
AUTOHIDE = 10              # seconds the shortcut's dock stays after you stop using it
TITLE = "Aura Dock"
GLASS_FAST = 1 / 15        # seconds between glass pictures while the background moves
GLASS_SLOW = 0.5           # ... and while it is still (saves CPU, e.g. during a game)
GLASS_MARGIN = 40          # extra pixels grabbed around the dock, so the blur has no dark edges
WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0x0, 0x11


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.wintypes.DWORD), ("biWidth", ctypes.wintypes.LONG),
                ("biHeight", ctypes.wintypes.LONG), ("biPlanes", ctypes.wintypes.WORD),
                ("biBitCount", ctypes.wintypes.WORD), ("biCompression", ctypes.wintypes.DWORD),
                ("biSizeImage", ctypes.wintypes.DWORD), ("biXPelsPerMeter", ctypes.wintypes.LONG),
                ("biYPelsPerMeter", ctypes.wintypes.LONG), ("biClrUsed", ctypes.wintypes.DWORD),
                ("biClrImportant", ctypes.wintypes.DWORD)]


def grab_screen(left, top, right, bottom):
    """
    A picture of part of the screen (real pixels). A plain Windows screen copy:
    ~8 ms, about 4x faster than PIL's ImageGrab, which is what makes a live glass possible.
    """
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    width, height = right - left, bottom - top
    screen = user32.GetDC(0)
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
    old = gdi32.SelectObject(memory, bitmap)
    try:
        gdi32.BitBlt(memory, 0, 0, width, height, screen, left, top, 0x00CC0020)  # SRCCOPY
        header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        pixels = ctypes.create_string_buffer(width * height * 4)
        gdi32.GetDIBits(memory, bitmap, 0, height, pixels, ctypes.byref(header), 0)
    finally:
        gdi32.SelectObject(memory, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(0, screen)
    return Image.frombuffer("RGB", (width, height), pixels, "raw", "BGRX", 0, 1)


class Pictures(tuple):
    """(blurred, sharp) URLs, plus .small: the tiny copy used to see if anything moved."""
    small = b""


def glass_pictures(hwnd, previous=None):
    """
    Picture of the screen right behind a window, blurred, plus the same picture
    unblurred: (blurred, sharp) data: URLs, or "" if it can't be taken.
    The window is hidden from the capture meanwhile. Used by the dock and the
    "task done" notification (notify.py).
    previous: the last result. If nothing behind the window changed, that same
    object is returned (no blur, no encoding), so callers can tell it is still.
    """
    if not hwnd:
        return ""
    user32, dwm = ctypes.windll.user32, ctypes.windll.dwmapi
    try:
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))  # real pixels
        w, h, m = rect.right - rect.left, rect.bottom - rect.top, GLASS_MARGIN
        user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)  # leave the window out
        try:
            dwm.DwmFlush()  # it takes effect when the screen is next drawn: wait for that
            dwm.DwmFlush()  # (twice, to be sure; a few ms each instead of a fixed 50 ms)
            shot = grab_screen(rect.left - m, max(0, rect.top - m), rect.right + m, rect.bottom + m)
        finally:
            user32.SetWindowDisplayAffinity(hwnd, WDA_NONE)  # screenshots can see it again
        top = rect.top - max(0, rect.top - m)
        # Blur: shrink, blur, grow back (fast). Then boost colours ("vibrancy") and
        # darken a little, so white text stays readable on a bright background.
        small = shot.resize((max(1, shot.width // 4), max(1, shot.height // 4)))
        if previous and previous.small == small.tobytes():
            return previous  # nothing moved behind the window
        blurred = small.filter(ImageFilter.GaussianBlur(5))
        blurred = ImageEnhance.Color(blurred).enhance(1.6)
        blurred = ImageEnhance.Brightness(blurred).enhance(0.9)
        box = (m, top, m + w, top + h)  # just the part behind the window
        # The blurred picture stays small (1/4): the page stretches it to fill the
        # window (100vw 100vh in the CSS), and blurred, nobody can tell. 20x less work.
        blurred = blurred.crop(tuple(round(v / 4) for v in box))
        # The sharp copy fills the thin ring between the window region (jagged:
        # Windows regions have no anti-aliasing) and the smooth CSS edge, so the
        # ring looks exactly like the screen behind it and the edge looks clean.
        result = Pictures((data_url(blurred), data_url(shot.crop(box))))
        result.small = small.tobytes()
        return result
    except Exception:
        return ""  # no picture: the page falls back to a plain dark glass colour


def data_url(image):
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def float_style(hwnd):
    """
    Switch off Windows 11's own rounding (only ~8px), its
    "system backdrop" (it ignores the cut-out shape: a dark box behind the
    corners) and its thin border, and hide the window from the taskbar.
    """
    if not hwnd:
        return
    user32, dwm = ctypes.windll.user32, ctypes.windll.dwmapi
    backdrop = ctypes.c_int(1)              # DWMWA_SYSTEMBACKDROP_TYPE (38) = NONE (1)
    dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(backdrop), ctypes.sizeof(backdrop))
    corner = ctypes.c_int(1)                # DWMWA_WINDOW_CORNER_PREFERENCE (33) = DONOTROUND (1)
    dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))
    no_border = ctypes.c_uint(0xFFFFFFFE)   # DWMWA_BORDER_COLOR (34) = none, no thin outline
    dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(no_border), ctypes.sizeof(no_border))
    # Tool window = no taskbar button; remove "app window" style
    style = user32.GetWindowLongW(hwnd, -20)
    user32.SetWindowLongW(hwnd, -20, (style | 0x80) & ~0x40000)


class Dock:
    def __init__(self, url, api):
        self.expanded = False
        self.visible = False
        self.shape = (*PILL, PILL[1] // 2)  # what is cut out now: (width, height, corner radius)
        self._target = self.shape            # what the page is animating towards
        self._generation = 0                 # newest shape request (older timers are ignored)
        self._shape_seq = 0                  # newest request number from the page (set_shape)
        self._shape_lock = threading.Lock()
        self._voice_lock = threading.Lock()  # held while the voice shortcut listens (push to talk)
        self._dnd_ours = False               # the focus timer turned Do Not Disturb on (see _quiet)
        if os.path.exists(DND_MARK):         # Aura stopped mid-timer last time: notifications come back
            notch.set_do_not_disturb(False)
            _remove(DND_MARK)
        self._pending_banner = None          # a notification that came while the notch was open (_flash)
        self._glass_run = 0                  # newest glass loop (an older one stops itself)
        self._last_glass = None
        self.position = config.DOCK_POSITION  # "top-mid", "bottom-left"... (config.DOCK_POSITIONS)
        self.autohide = config.DOCK_AUTOHIDE
        self.style = config.DOCK_STYLE        # "dock" (glass pill) or "notch" (black, stuck to the top edge)
        self.main_minimized = False           # the main window is minimised (then the dock normally shows)
        self._hide_run = 0                    # newest autohide timer (older ones are ignored)
        self.media = NowPlaying(self._on_media)  # what is playing (only watched while the dock shows)
        self.timer = None                     # notch focus timer: {"end": epoch ms, "total": ms} or None
        self._timer_run = 0
        self._flash_run = 0
        self._flash_opened = False            # a HUD / banner showed the notch, so it hides it again
        # Notch-only watchers, started once the page is ready (see _watch)
        self.hud = Hud(self._on_hud)          # volume / brightness bars
        self.devices = Devices(self._on_device)  # charger, Bluetooth
        self.clipboard = Clipboard(self._on_copy)  # the clipboard shelf
        self.window = webview.create_window(
            TITLE, url=url, js_api=api,
            width=WIDTH, height=HEIGHT, min_size=(100, 30),
            frameless=True, on_top=True, resizable=False, hidden=True,
            easy_drag=False,  # it stays where Settings > Dock puts it: no dragging it around
            shadow=False, background_color="#0b0c18",
        )
        self.window.events.loaded += self._watch

    # ---------- Windows helpers ----------
    def _hwnd(self):
        """This dock's own Windows handle (not found by title, so two copies of Aura can't mix up)."""
        native = getattr(self.window, "native", None)
        if sys.platform != "win32" or native is None:
            return 0
        return int(native.Handle.ToInt64())

    def _pill(self):
        """The collapsed shape (width, height, corner radius) for this style."""
        return (*NOTCH, CARD_RADIUS) if self.style == "notch" else (*PILL, PILL[1] // 2)

    def _pos(self):
        """Where it sits: the notch is always at the top middle."""
        return "top-mid" if self.style == "notch" else self.position

    def _style(self):
        """Once per show (see float_style)."""
        float_style(self._hwnd())

    def _cut(self, width, height, radius):
        """
        Cut the window down to a rounded box of this size (page pixels), at the
        window's edge or corner the dock sits in (dock.css puts the page there too).
        Everything outside it is invisible and lets clicks through.
        """
        hwnd = self._hwnd()
        if not hwnd:
            return
        user32 = ctypes.windll.user32
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        scale = user32.GetDpiForWindow(hwnd) / 96  # real pixels per page pixel (display scaling)
        free_x = rect.right - rect.left - round(width * scale)  # room beside / below the dock
        free_y = rect.bottom - rect.top - round(height * scale)
        vertical, side = self._pos().split("-")
        left = {"left": 0, "mid": free_x // 2, "right": free_x}[side]
        top = free_y if vertical == "bottom" else 0
        right, bottom = left + round(width * scale), top + round(height * scale)
        diameter = round(2 * radius * scale)
        # Notch: start the rounded box above the window, so its top corners fall outside (square)
        gdi32 = ctypes.windll.gdi32
        region = gdi32.CreateRoundRectRgn(left, top - (diameter if self.style == "notch" else 0),
                                          right + 1, bottom + 1, diameter, diameter)
        if self.style == "notch":  # room for the curved shoulders that join it to the screen's top edge (dock.css)
            ring, ear = round(2 * scale), round(NOTCH_EAR * scale)
            ears = gdi32.CreateRectRgn(left + ring - ear, 0, right - ring + ear + 1, ear)
            gdi32.CombineRgn(region, region, ears, 2)  # RGN_OR
            gdi32.DeleteObject(ears)
        user32.SetWindowRgn(hwnd, region, True)  # Windows owns the region now
        self.shape = (width, height, radius)

    def set_shape(self, width, height, radius, seq=0):
        """
        The page is animating the dock to this size (dock.js). Cut-out shapes can't
        animate, so: the cut-out grows at once to cover both the old and the new
        size, and only after the animation does it shrink to the new size. Meanwhile
        the extra area shows the sharp picture of the screen behind (dock.css), so
        it can't be seen.
        """
        with self._shape_lock:
            if seq and seq < self._shape_seq:
                return  # an older request that arrived late (e.g. "pill" after "banner")
            self._shape_seq = seq or self._shape_seq
            old = self.shape
            self._target = (width, height, radius)
            self._generation += 1
            generation = self._generation
        bigger = (max(old[0], width), max(old[1], height), max(old[2], radius))
        self._cut(*bigger)

        def settle():
            if generation == self._generation:  # no newer size came in meanwhile
                self._cut(*self._target)
        threading.Timer(ANIMATION + 0.05, settle).start()

    def _place(self):
        """Put the window in its corner / edge of the screen, using its REAL size (see WIDTH).
        At the bottom it stays above the taskbar."""
        hwnd = self._hwnd()
        if not hwnd:
            return
        user32 = ctypes.windll.user32
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.wintypes.DWORD), ("rcMonitor", ctypes.wintypes.RECT),
                        ("rcWork", ctypes.wintypes.RECT), ("dwFlags", ctypes.wintypes.DWORD)]
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 1), ctypes.byref(info))  # 1 = primary if none
        screen, work = info.rcMonitor, info.rcWork
        width, height = rect.right - rect.left, rect.bottom - rect.top
        margin = round(MARGIN * user32.GetDpiForWindow(hwnd) / 96)
        vertical, side = self._pos().split("-")
        if self.style == "notch":
            margin = 0  # stuck to the top edge of the screen
        x = {"left": work.left + margin, "mid": screen.left + (screen.right - screen.left - width) // 2,
             "right": work.right - width - margin}[side]
        y = work.bottom - height - margin if vertical == "bottom" else screen.top + margin
        user32.SetWindowPos(hwnd, 0, x, y, 0, 0, 0x0001 | 0x0004 | 0x0010)  # NOSIZE | NOZORDER | NOACTIVATE

    def _reshape(self):
        """pywebview shows the window a moment later, which resets the cut-out, so apply it again after."""
        for delay in (0.1, 0.3, 0.8):
            threading.Timer(delay, lambda: self._cut(*self._target)).start()

    def _glass(self):
        """(blurred, sharp) pictures of the screen behind the dock (see glass_pictures)."""
        return glass_pictures(self._hwnd(), self._last_glass)

    def _update_glass(self):
        """Take a new glass picture and give it to the page. True if anything behind the dock moved."""
        urls = self._glass()
        if not urls or urls is self._last_glass:
            return False
        self._last_glass = urls
        self.window.evaluate_js(f"setGlass('{urls[0]}', '{urls[1]}')")
        return True

    def _glass_loop(self, run):
        """Keep the glass live while the dock is on screen: fast while things move behind it."""
        wait = GLASS_SLOW
        while self.visible and run == self._glass_run:
            time.sleep(wait)
            if self.visible and run == self._glass_run:
                try:
                    wait = GLASS_FAST if self._update_glass() else GLASS_SLOW
                except Exception:
                    wait = GLASS_SLOW  # e.g. the window closed meanwhile

    # ---------- Called by the main window's events ----------
    def on_minimized(self):
        self.main_minimized = True
        if not self.autohide:
            self.show()

    def on_restored(self):
        self.main_minimized = False
        self.hide()

    def show(self, wait=0.3):
        self.expanded = False
        self.visible = True
        time.sleep(wait)  # let the main window's minimise animation finish
        self._target = self._pill()
        self._send_look()
        self._place()
        self._style()
        self._cut(*self._target)
        self._update_glass()  # before showing the dock
        self.window.show()  # pywebview's show() also makes the page inside visible
        self.window.evaluate_js("setDockMode('pill', true)")  # true = no animation
        self._reshape()
        self._glass_run += 1  # hide + show quickly, or two show events: only this loop keeps running
        threading.Thread(target=self._glass_loop, args=(self._glass_run,), daemon=True).start()
        self.media.start()

    def hide(self):
        self.visible = False
        self.expanded = False
        self.media.stop()
        self.window.hide()

    def _on_media(self, info):
        """What is playing changed (nowplaying.py): tell the page. The cover only comes with a new song."""
        if not self.visible:
            return
        thumb = info.pop("thumb", None)
        if thumb is not None:
            mime = "image/png" if thumb.startswith("iVBOR") else "image/jpeg"  # base64 of a PNG file always starts "iVBOR"
            art = f"data:{mime};base64,{thumb}" if thumb else ""
            self.window.evaluate_js(f"setMediaArt({json.dumps(art)})")
        self.window.evaluate_js(f"setMedia({json.dumps(info)})")

    # ---------- Called from the dock page (through Api) ----------
    def expand(self):
        self.expanded = True
        hwnd = self._hwnd()
        if hwnd:
            ctypes.windll.user32.SetForegroundWindow(hwnd)  # so you can type straight away

    def collapse(self):
        self.expanded = False
        self._arm_autohide()
        pending, self._pending_banner = self._pending_banner, None
        if pending:  # it came while the notch was open
            threading.Timer(0.6, self._flash, pending).start()  # after the shrink animation

    # ---------- The shortcut (hotkey.py) and settings ----------
    def shortcut(self):
        """The dock's shortcut was pressed: open the dock ready to type, or close it again."""
        if not self.visible:
            self.show(wait=0)
            time.sleep(0.15)  # let the window appear before it grows
        if self.expanded:
            self.window.evaluate_js("collapseDock()")
        else:
            self.window.evaluate_js("expandDock()")
            self._arm_autohide()

    def voice_shortcut(self):
        """The voice shortcut was pressed: listen while it is held (a wave in the closed notch),
        then open the dock with what was said in its box. Enter runs it."""
        if not self._voice_lock.acquire(blocking=False):
            return  # already listening
        threading.Thread(target=self._push_to_talk, daemon=True).start()

    def _push_to_talk(self):
        try:
            keys = hotkey.parse(config.VOICE_SHORTCUT)
            key = hotkey.VK_SPACE if keys == hotkey.BACKTICK_SPACE else keys[1] if keys else 0
            if not self.visible:
                self.show(wait=0)
            try:
                voice.start()
            except voice.VoiceError as e:
                self.window.evaluate_js(f"voiceDone('', {json.dumps(str(e))})")
                return
            self.window.evaluate_js("setListening(true)")
            started = time.time()
            # Until the key is let go (the time limit is voice.py's too)
            while key and ctypes.windll.user32.GetAsyncKeyState(key) & 0x8000 \
                    and time.time() - started < config.MAX_VOICE_SECONDS:
                time.sleep(0.03)
            self.window.evaluate_js("setListening('working')")
            try:
                text = voice.stop()
                error = "" if text else "I didn't hear anything. Hold the keys while you talk."
            except voice.VoiceError as e:
                text, error = "", str(e)
            self.window.evaluate_js(f"voiceDone({json.dumps(text)}, {json.dumps(error)})")
            self._arm_autohide()
        except Exception as e:  # e.g. Aura closed meanwhile
            print(f"  [voice shortcut] {e}")
        finally:
            self._voice_lock.release()

    def _arm_autohide(self):
        """Hide AUTOHIDE seconds from now, unless it is in use then. Only with autohide on,
        or when the shortcut opened the dock while Aura's window is open."""
        self._hide_run += 1
        run = self._hide_run

        def check():
            if run != self._hide_run or not self.visible or (not self.autohide and self.main_minimized):
                return
            if self.expanded:
                threading.Timer(AUTOHIDE, check).start()  # still typing / reading: look again later
            else:
                self.hide()
        threading.Timer(AUTOHIDE, check).start()

    def _send_look(self):
        """Tell the page where the dock sits and whether it is a notch (dock.css lines it up)."""
        self.window.evaluate_js(f"setDockPosition({json.dumps(self._pos())}, {json.dumps(self.style)})")

    def set_position(self, position):
        self.position = position
        self._relook()

    def set_style(self, style):
        self.style = style
        self._watch()
        self._relook()

    def _watch(self):
        """The notch's watchers run only in notch style."""
        for watcher in (self.hud, self.devices, self.clipboard):
            watcher.start() if self.style == "notch" else watcher.stop()

    def stop_watchers(self):
        for watcher in (self.hud, self.devices, self.clipboard):
            watcher.stop()
        self._quiet(False)  # Aura closing during a focus timer: notifications come back

    # ---------- Notch: focus timer and volume / brightness HUD ----------
    def _quiet(self, on):
        """Focus timer: Windows' Do Not Disturb on while it runs. Only turned off again if
        Aura turned it on (if you had it on already, it stays on)."""
        if on and not self._dnd_ours and notch.do_not_disturb() == 0:
            self._dnd_ours = notch.set_do_not_disturb(True)
            if self._dnd_ours:
                open(DND_MARK, "w").close()  # if Aura crashes now, the next start turns it off (see __init__)
        elif not on and self._dnd_ours:
            notch.set_do_not_disturb(False)
            self._dnd_ours = False
            _remove(DND_MARK)

    def set_timer(self, seconds):
        """Start a focus timer (seconds > 0) or stop it (0). It chimes and shows the notch when done.
        System notifications are muted (Do Not Disturb) while it runs."""
        self._timer_run += 1
        run = self._timer_run
        if seconds <= 0:
            self.timer = None
            self._quiet(False)
        else:
            self.timer = {"end": int(time.time() * 1000 + seconds * 1000), "total": int(seconds * 1000)}
            self._quiet(True)

            def done():
                if run != self._timer_run:
                    return  # stopped or restarted meanwhile
                self.timer = None
                self._quiet(False)
                from notify import play_chime  # here: notify.py imports this file
                play_chime("remind")
                self.window.evaluate_js("setTimer(null)")
                self.banner("remind", "Time's up", "Your focus timer has finished", seconds=6)
            threading.Timer(seconds, done).start()
        self.window.evaluate_js(f"setTimer({json.dumps(self.timer)})")
        return self.timer

    def _flash(self, script, seconds, end_script):
        """Show something in the closed notch for a moment (a HUD bar, a banner).
        If the notch was hidden it shows just for that and hides again afterwards.
        Nothing is shown while the notch is open (you are using it)."""
        if self.style != "notch":
            return
        if self.expanded:
            if "showBanner" in script:  # a notification while you use the notch: show it once you close it
                self._pending_banner = (script, seconds, end_script)
            return
        self._flash_run += 1
        run = self._flash_run
        if not self.visible:
            self._flash_opened = True
            self.show(wait=0)
        self.window.evaluate_js(script)

        def end():
            if run != self._flash_run:
                return  # a newer one is showing
            try:
                self.window.evaluate_js(end_script)
                if self._flash_opened and not self.expanded:
                    self._flash_opened = False
                    if not (self.main_minimized and not self.autohide):  # it normally isn't on screen now
                        time.sleep(0.5)  # let the banner shrink back first
                        self.hide()
            except Exception:
                pass  # Aura was closed meanwhile
        threading.Timer(seconds, end).start()

    def _on_hud(self, kind, percent, muted):
        """Volume or brightness changed (notch.Hud): the bar shows for a moment."""
        self._flash(f"setHud({json.dumps(kind)}, {int(percent)}, {json.dumps(bool(muted))})", 1.8, "setHud(null)")

    def banner(self, kind, title, text="", seconds=4.5, snooze=None):
        """A notification in the notch (like the iPhone's Dynamic Island): kind picks the
        icon and colour (ok, error, warn, info, remind, charger, bluetooth, clip, ai).
        snooze: a reminder's id, for its Snooze button."""
        info = {"kind": kind, "title": str(title)[:90], "text": str(text)[:140], "snooze": snooze}
        self._flash(f"showBanner({json.dumps(info)})", seconds, "showBanner(null)")

    def _on_device(self, kind, info):
        if kind == "charger":
            self.banner("charger", "Charging" if info["charging"] else "Charger unplugged", f"Battery {info['percent']}%")
        elif kind == "bluetooth":
            battery = f"Battery {info['battery']}%" if info.get("battery") is not None else "Bluetooth"
            self.banner("bluetooth", f"{info['name']} connected", battery)

    def _on_copy(self, text, count_today):
        preview = " ".join(text.split())[:70]
        self.banner("clip", "Copied to the shelf", f"{preview} · {count_today} today", seconds=2.5)
        self.window.evaluate_js(f"setShelf({json.dumps(self.clipboard.items)})")

    def _relook(self):
        if self.visible:
            self._send_look()
            self._place()
            if self.expanded:
                self._cut(*self._target)
            else:
                self.set_shape(*self._pill())  # notch and pill differ in size: page animates, cut follows

    def set_autohide(self, on):
        self.autohide = on
        if self.main_minimized and not on and not self.visible:
            self.show(wait=0)
        elif on and self.visible and not self.expanded:
            self._arm_autohide()
