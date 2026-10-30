"""
hotkey.py - The dock's keyboard shortcut (e.g. Ctrl+Alt+Space), working in every app.

Windows' RegisterHotKey tells us when the keys are pressed, wherever you are.
A hotkey belongs to the thread that registered it, so each shortcut gets its own
small thread that waits for Windows' messages; changing the shortcut stops that
thread and starts a new one.

The default, ` + Space, can't be a Windows hotkey (` isn't Ctrl / Alt / Shift / Win),
so it is watched with a keyboard hook instead (_watch_backtick): a ` press is held
back for a moment; Space next opens the dock, anything else types the ` as usual.
"""

import ctypes
import ctypes.wintypes
import sys
import threading

MODIFIERS = {"Ctrl": 0x2, "Alt": 0x1, "Shift": 0x4, "Win": 0x8}
NO_REPEAT = 0x4000  # holding the keys down fires once
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
BACKTICK_SPACE = "`+Space"
VK_BACKTICK, VK_SPACE = 0xC0, 0x20
WH_KEYBOARD_LL = 13
OURS = 0xA0A0  # tag on the keys the hook types itself, so it lets them straight through
DOWN, UP = (0x100, 0x104), (0x101, 0x105)  # WM_KEYDOWN / WM_SYSKEYDOWN, WM_KEYUP / WM_SYSKEYUP
HELD_MODIFIERS = (0x10, 0x11, 0x12, 0x5B, 0x5C)  # Shift, Ctrl, Alt, Win: ` with these is left alone


class KeyInfo(ctypes.Structure):  # KBDLLHOOKSTRUCT
    _fields_ = [("vk", ctypes.wintypes.DWORD), ("scan", ctypes.wintypes.DWORD), ("flags", ctypes.wintypes.DWORD),
                ("time", ctypes.wintypes.DWORD), ("extra", ctypes.c_size_t)]


HOOK_PROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
KEYS = {"Space": 0x20, "Enter": 0x0D, "Tab": 0x09, "Backspace": 0x08, "Insert": 0x2D, "Delete": 0x2E,
        "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PageDown": 0x22,
        "Up": 0x26, "Down": 0x28, "Left": 0x25, "Right": 0x27,
        "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE,
        ",": 0xBC, ".": 0xBE, "/": 0xBF, "\\": 0xDC}
KEYS.update({chr(c): c for c in range(ord("A"), ord("Z") + 1)})
KEYS.update({str(n): ord(str(n)) for n in range(10)})
KEYS.update({f"F{n}": 0x6F + n for n in range(1, 25)})


def parse(text):
    """"Ctrl+Alt+Space" -> (modifier flags, key code), or None if it isn't a usable shortcut.
    "`+Space" -> BACKTICK_SPACE (watched with a hook, see _watch_backtick)."""
    if str(text or "").replace(" ", "") == BACKTICK_SPACE:
        return BACKTICK_SPACE
    parts = [p.strip() for p in str(text or "").split("+") if p.strip()]
    if len(parts) < 2 or parts[-1] not in KEYS or len(set(parts[:-1])) != len(parts) - 1:
        return None
    if any(p not in MODIFIERS for p in parts[:-1]):
        return None
    flags = 0
    for p in parts[:-1]:
        flags |= MODIFIERS[p]
    return flags, KEYS[parts[-1]]


class Hotkey:
    def __init__(self, on_press):
        self.on_press = on_press
        self.text = ""  # the shortcut listened for now
        self._thread = None
        self._thread_id = 0

    def set(self, text):
        """Listen for this shortcut instead of the old one. False if it can't be used (bad, or taken by another app)."""
        keys = parse(text)
        if not keys or sys.platform != "win32":
            return False
        old = self.text
        self.stop()
        if self._start(keys):
            self.text = text
            return True
        if old:
            self._start(parse(old))  # keep the old shortcut working
            self.text = old
        return False

    def _start(self, keys):
        result = {}
        ready = threading.Event()
        target = self._watch_backtick if keys == BACKTICK_SPACE else self._listen
        self._thread = threading.Thread(target=target, args=(keys, result, ready), daemon=True)
        self._thread.start()
        ready.wait(2)
        return result.get("ok", False)

    def stop(self):
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(1)  # it unregisters the keys as it ends, so they can be registered again
        self._thread_id, self.text = 0, ""

    def _press(self):
        threading.Thread(target=self.on_press, daemon=True).start()  # the hook must answer Windows fast

    def _watch_backtick(self, keys, result, ready):
        """` + Space through a low-level keyboard hook (see the top of this file)."""
        user32 = ctypes.windll.user32
        user32.CallNextHookEx.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM)
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOK_PROC, ctypes.c_void_p, ctypes.wintypes.DWORD)
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        state = {"held": False, "used": False, "space_up": False}

        def type_key(vk, scan, flags):  # our own key presses come back tagged OURS and pass through
            user32.keybd_event(vk, scan, flags, OURS)  # flags: 1 = extended key, 2 = key up

        def on_key(code, kind, data):
            key = ctypes.cast(data, ctypes.POINTER(KeyInfo)).contents
            if code < 0 or key.extra == OURS:
                return user32.CallNextHookEx(None, code, kind, data)
            if key.vk == VK_BACKTICK:
                if kind in DOWN:
                    if state["held"]:
                        return 1  # held down: the repeats are held back too
                    if not any(user32.GetAsyncKeyState(m) & 0x8000 for m in HELD_MODIFIERS):
                        state.update(held=True, used=False)
                        return 1
                elif state["held"]:  # let go without Space: type the ` now
                    state["held"] = False
                    if not state["used"]:
                        type_key(VK_BACKTICK, key.scan, 0)
                        type_key(VK_BACKTICK, key.scan, 2)  # 2 = key up
                    return 1
            elif state["held"] and kind in DOWN:
                if key.vk == VK_SPACE:
                    state.update(used=True, space_up=True)
                    self._press()
                    return 1
                # another key while ` is down: type the ` first, then that key, in order
                state["held"] = False
                type_key(VK_BACKTICK, 0, 0)
                type_key(VK_BACKTICK, 0, 2)
                type_key(key.vk, key.scan, key.flags & 1)
                return 1
            elif key.vk == VK_SPACE and kind in UP and state["space_up"]:
                state["space_up"] = False
                return 1  # its key-down went to the dock, so the app never sees this Space
            return user32.CallNextHookEx(None, code, kind, data)

        proc = HOOK_PROC(on_key)  # kept alive for as long as the hook runs
        hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, proc, None, 0)
        result["ok"] = bool(hook)
        if hook:
            self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        ready.set()
        if not hook:
            return
        message = ctypes.wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                pass
        finally:
            user32.UnhookWindowsHookEx(ctypes.c_void_p(hook))

    def _listen(self, keys, result, ready):
        user32 = ctypes.windll.user32
        result["ok"] = bool(user32.RegisterHotKey(None, 1, keys[0] | NO_REPEAT, keys[1]))
        if result["ok"]:
            self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        ready.set()
        if not result["ok"]:
            return
        message = ctypes.wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:  # 0 = WM_QUIT (stop)
                if message.message == WM_HOTKEY:
                    try:
                        self.on_press()
                    except Exception as e:  # a failed press must not end the listening
                        print(f"  [hotkey] {e}")
        finally:
            user32.UnregisterHotKey(None, 1)


if __name__ == "__main__":  # quick self-check of the parser
    assert parse("Ctrl+Alt+Space") == (0x3, 0x20)
    assert parse("Shift+F5") == (0x4, 0x74)
    assert parse("Ctrl+k") is None and parse("Space") is None and parse("Ctrl+Ctrl+A") is None
    assert parse("Foo+A") is None
    assert parse("` + Space") == BACKTICK_SPACE
    print("ok")
