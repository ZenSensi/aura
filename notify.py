"""
notify.py - The "task done" notification: a small frosted-glass card at the
top-right of the SCREEN (like a phone notification), shown when a request
finishes, so you see it even while you are using other apps.

How it works (the same tricks as the dock, see dock.py):
  - It is a third pywebview window: frameless, always on top, hidden at start,
    and it never takes the keyboard focus away from what you are doing.
  - A window region cuts it to the card's shape; the page draws the smooth
    rounded edge and the live blurred "glass" background.
  - The page (static/notify.html) slides the card in, waits, slides it out and
    asks Python to hide the window. Clicking the card opens Aura.
  - A soft "pop" chime plays as it appears (see play_chime): two quick notes
    going up when the task is done, going down when there was a problem.
    Python makes the sound itself (no sound file to ship) and Windows plays it,
    so it follows your normal volume.
"""

import ctypes
import ctypes.wintypes
import json
import math
import os
import struct
import sys
import tempfile
import threading
import time
import wave

import webview

from dock import GLASS_FAST, GLASS_SLOW, float_style, glass_pictures

WIDTH, HEIGHT = 396, 260   # asked size; pywebview makes frameless windows ~16 x 39 px smaller
MARGIN = 16                # gap from the screen's top-right corner
RADIUS = 20                # the card's corner radius (18) + the 2px ring (see notify.css)
TITLE = "Aura Notification"
SW_HIDE, SW_SHOWNOACTIVATE = 0, 4

# The chime for each kind: (note in Hz, start in seconds). Up = good, down = a problem.
CHIMES = {
    "ok":    [(880.0, 0.0), (1318.5, 0.075)],   # A5 -> E6
    "info":  [(784.0, 0.0), (1046.5, 0.075)],   # G5 -> C6
    "warn":  [(987.8, 0.0), (740.0, 0.09)],     # B5 -> F#5
    "error": [(659.3, 0.0), (493.9, 0.10)],     # E5 -> B4
    "remind": [(880.0, 0.0), (1108.7, 0.09), (1318.5, 0.18)],  # A5 -> C#6 -> E6: a little bell
}
CHIME_VOLUME = 0.22   # 0..1, kept soft
RATE = 44100          # samples per second


def chime_file(kind):
    """Make the chime's .wav once (in the temp folder) and return its path."""
    path = os.path.join(tempfile.gettempdir(), f"aura_chime_{kind}.wav")
    if os.path.isfile(path):
        return path
    notes = CHIMES.get(kind, CHIMES["info"])
    length = int(RATE * (notes[-1][1] + 0.32))
    samples = [0.0] * length
    for freq, start in notes:
        first = int(start * RATE)
        for i in range(length - first):
            t = i / RATE
            envelope = min(1.0, t / 0.004) * math.exp(-t * 16)  # 4ms attack, quick soft fade
            tone = math.sin(2 * math.pi * freq * t) + 0.18 * math.sin(4 * math.pi * freq * t)  # + a little warmth
            samples[first + i] += envelope * tone
    peak = max(abs(s) for s in samples) or 1.0
    frames = b"".join(struct.pack("<h", int(32767 * CHIME_VOLUME * s / peak)) for s in samples)
    with wave.open(path, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(frames)
    return path


def play_chime(kind):
    """Play the chime without waiting for it. A sound problem never stops the notification."""
    try:
        import winsound  # Windows only
        winsound.PlaySound(chime_file(kind), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        pass


class Notifier:
    def __init__(self, url, api):
        self.visible = False
        self._ready = threading.Event()  # the page has loaded
        self._lock = threading.Lock()    # one notification at a time
        self.window = webview.create_window(
            TITLE, url=url, js_api=api,
            width=WIDTH, height=HEIGHT,
            frameless=True, on_top=True, resizable=False, hidden=True, focus=False,
            shadow=False, background_color="#0b0c18",
        )
        self.window.events.loaded += self._ready.set

    def _hwnd(self):
        native = getattr(self.window, "native", None)
        if sys.platform != "win32" or native is None:
            return 0
        return int(native.Handle.ToInt64())

    def _place(self, hwnd):
        """Top-right corner of the screen's work area (above the taskbar, if it is up there)."""
        user32 = ctypes.windll.user32
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.wintypes.DWORD), ("rcMonitor", ctypes.wintypes.RECT),
                        ("rcWork", ctypes.wintypes.RECT), ("dwFlags", ctypes.wintypes.DWORD)]
        info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
        user32.GetMonitorInfoW(user32.MonitorFromPoint(ctypes.wintypes.POINT(0, 0), 1), ctypes.byref(info))
        margin = round(MARGIN * user32.GetDpiForWindow(hwnd) / 96)
        x = info.rcWork.right - margin - (rect.right - rect.left)
        y = info.rcWork.top + margin
        # HWND_TOPMOST (-1, passed as a 64-bit handle: a plain -1 is refused), NOSIZE | NOACTIVATE
        user32.SetWindowPos(ctypes.wintypes.HWND(hwnd), ctypes.wintypes.HWND(-1), x, y, 0, 0, 0x0001 | 0x0010)

    def cut(self, height):
        """Cut the window to the card (page pixels, ring included; the card fills the width)."""
        hwnd = self._hwnd()
        if not hwnd:
            return
        user32 = ctypes.windll.user32
        rect = ctypes.wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        scale = user32.GetDpiForWindow(hwnd) / 96
        diameter = round(2 * RADIUS * scale)
        region = ctypes.windll.gdi32.CreateRoundRectRgn(
            0, 0, rect.right - rect.left + 1, round(height * scale) + 1, diameter, diameter)
        user32.SetWindowRgn(hwnd, region, True)

    def notify(self, kind, title, text, snooze=None):
        """
        Show one notification. kind: "ok" | "warn" | "error" | "info" | "remind".
        snooze: a reminder's id: the card gets Snooze / Done buttons and stays longer.
        Runs in the background, so a finished request never waits for it.
        """
        threading.Thread(target=self._show, args=(kind, title, text, snooze), daemon=True).start()

    def _show(self, kind, title, text, snooze=None):
        if not self._ready.wait(10):
            return  # the page never loaded
        with self._lock:
            hwnd = self._hwnd()
            if not hwnd:
                return
            float_style(hwnd)
            self._place(hwnd)
            self.cut(0)  # nothing visible until the page has measured the card
            pictures = glass_pictures(hwnd)  # (the window is still hidden, so it's not in them)
            if pictures:
                self.window.evaluate_js(f"setGlass({json.dumps(pictures[0])}, {json.dumps(pictures[1])})")
            ctypes.windll.user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)  # show without taking the focus
            already_showing, self.visible = self.visible, True
            self.window.evaluate_js(f"notify({json.dumps({'kind': kind, 'title': title, 'text': text, 'snooze': snooze})})")
            play_chime(kind)  # as the card slides in
        if not already_showing:  # a newer notification just replaces the text: one loop is enough
            threading.Thread(target=self._glass_loop, daemon=True).start()

    def _glass_loop(self):
        """Keep the glass live while the card is on screen: fast while things move behind it."""
        last, wait = None, GLASS_SLOW
        while self.visible:
            time.sleep(wait)
            try:
                pictures = glass_pictures(self._hwnd(), last) if self.visible else ""
                wait = GLASS_SLOW
                if pictures and pictures is not last and self.visible:
                    last, wait = pictures, GLASS_FAST
                    self.window.evaluate_js(f"setGlass({json.dumps(pictures[0])}, {json.dumps(pictures[1])})")
            except Exception:
                return  # e.g. Aura was closed meanwhile

    def hide(self):
        """The page finished its slide-out animation (or the card was clicked)."""
        self.visible = False
        hwnd = self._hwnd()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, SW_HIDE)
