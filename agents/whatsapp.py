"""
agents/whatsapp.py - The WhatsApp agent.

Sends a message to a saved contact on WhatsApp Desktop: either YOUR exact
words (e.g. "send hi to mahi" sends "hi") or the message the Writer just wrote.

NO AI MODEL, like the Mailer, because sending cannot be undone:
  - It only sends to a contact whose name YOU typed in the request,
    looked up in YOUR contacts file (contacts.json next to the app).
    A model can never make up or change the phone number.
  - Exact words are only sent if they appear word for word in what you typed,
    so a model can never make up or change them either.
  - Otherwise it only sends the message written (or the answer worked out) in the
    same request, or in the previous one for follow-ups like "send this answer to mummy".
    It refuses drafts that still have blanks like [Name].

How it sends (WhatsApp has no API for personal accounts):
  1. Opens WhatsApp's own link  whatsapp://send?phone=<number>, which opens that chat.
     (Its "&text=" part is ignored by WhatsApp Desktop, so we type the message.)
  2. Waits until the WhatsApp window is in front, clears the message box,
     types the message (Windows SendInput) and presses Enter.
     Before every step it checks that WhatsApp is still the window in front;
     if not, it stops, so keypresses can never land in another app.
"""

import ctypes
import ctypes.wintypes
import json
import os
import re
import sys
import time
import urllib.parse

import config

MAX_LENGTH = 1000         # longest message we send
WAIT_FOR_WINDOW = 20      # seconds to wait for WhatsApp to come to the front
SETTLE_RUNNING = 2.5      # seconds for the chat to load if WhatsApp was already open
SETTLE_STARTING = 7.0     # ... and if WhatsApp had to start first
SETTLE_REFOCUS = 1.0      # seconds after bringing WhatsApp back to the front
FOCUS_TRIES = 3           # times we bring WhatsApp back if another window takes the front


def make_result(action, target, message, start):
    """Result in the same shape as the System agent's, so the UI and logs can show it."""
    return {
        "agent": "whatsapp", "model": "none (plain Python)", "seconds": round(time.time() - start, 2),
        "fallback_used": False,
        "action": {"action": action, "target": target},
        "message": message,
    }


def refuse(reason, start):
    return make_result("refuse", "", "Not sent: " + reason, start)


def load_contacts():
    """{"mahi": "919876543210", ...} from contacts.json (names lower-case, numbers digits only)."""
    try:
        with open(config.CONTACTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise ValueError("contacts.json is not valid JSON. See contacts.example.json.")
    contacts = {}
    for name, number in data.items():
        digits = re.sub(r"\D", "", str(number))
        if 8 <= len(digits) <= 15:
            contacts[str(name).strip().lower()] = digits
    return contacts


NUMBER_PATTERN = re.compile(r"\+?\(?\d[\d\s().-]{6,}\d")


def parse_contacts_text(text):
    """A .txt of contacts, one per line: "Mahi, +91 98765 43210" (or "Mahi: ...", "Mahi - ...", "+91... Mahi").
    Returns ({name: number}, lines skipped). A line needs a name and an 8-15 digit number."""
    found, skipped = {}, 0
    for line in text.splitlines():
        if not line.strip():
            continue
        match = NUMBER_PATTERN.search(line)
        digits = re.sub(r"\D", "", match.group()) if match else ""
        name = (line[:match.start()] + " " + line[match.end():]).strip(" \t,;:|-=") if match else ""
        name = re.sub(r"\s+", " ", name)[:60]
        if not name or not 8 <= len(digits) <= 15:
            skipped += 1
            continue
        found[name] = ("+" if match.group().startswith("+") else "") + digits
    return found, skipped


def contact_in_request(request, contacts):
    """Names from contacts.json that the user typed in the request (whole words only)."""
    text = request.lower()
    return [name for name in contacts if re.search(r"\b" + re.escape(name) + r"\b", text)]


# ---------- Windows helpers (ctypes) ----------
def is_whatsapp(hwnd):
    """True if the window belongs to WhatsApp (checked by its program file, not just its title)."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    pid = ctypes.wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        return False
    path = ctypes.create_unicode_buffer(512)
    size = ctypes.wintypes.DWORD(512)
    ok = kernel32.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size))
    kernel32.CloseHandle(process)
    return bool(ok) and "whatsapp" in os.path.basename(path.value).lower()


def front_window_is_whatsapp():
    """True if the window in front belongs to WhatsApp."""
    return is_whatsapp(ctypes.windll.user32.GetForegroundWindow())


def find_whatsapp_window():
    """The main WhatsApp window (visible, titled "WhatsApp"), or None."""
    found = []
    user32 = ctypes.windll.user32

    def check(hwnd, _):
        title = ctypes.create_unicode_buffer(64)
        user32.GetWindowTextW(hwnd, title, 64)
        if user32.IsWindowVisible(hwnd) and title.value == "WhatsApp" and is_whatsapp(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)(check), 0)
    return found[0] if found else None


def whatsapp_is_open():
    """True if a WhatsApp window is already open (so the chat loads faster)."""
    return find_whatsapp_window() is not None


def bring_whatsapp_to_front(link):
    """Put WhatsApp back in front after another window (e.g. Notepad opened by an
    earlier step) took its place. True if WhatsApp is in front afterwards."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = find_whatsapp_window()
    if hwnd:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        # Windows only lets the app in front move another window to the front, so we
        # briefly join the input of the window that is in front now
        front_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        own_thread = kernel32.GetCurrentThreadId()
        user32.AttachThreadInput(own_thread, front_thread, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(own_thread, front_thread, False)
        time.sleep(0.5)
    if not front_window_is_whatsapp():
        os.startfile(link)  # opening the chat link again makes WhatsApp come to the front itself
    deadline = time.time() + 5
    while not front_window_is_whatsapp():
        if time.time() > deadline:
            return False
        time.sleep(0.3)
    return True


VK_RETURN, VK_SHIFT, VK_CONTROL, VK_BACK, VK_END, VK_A = 0x0D, 0x10, 0x11, 0x08, 0x23, 0x41
KEYUP, UNICODE = 0x2, 0x4  # KEYEVENTF_KEYUP, KEYEVENTF_UNICODE


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.wintypes.WORD), ("wScan", ctypes.wintypes.WORD), ("dwFlags", ctypes.wintypes.DWORD),
                ("time", ctypes.wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    # Windows' INPUT is a union of mouse/keyboard/hardware input; padding keeps it the right size
    _fields_ = [("type", ctypes.wintypes.DWORD), ("ki", KEYBDINPUT), ("padding", ctypes.c_ubyte * 8)]


def _send(events):
    """Send keyboard events. Each event: (virtual key, unicode char code, flags)."""
    inputs = (INPUT * len(events))(*[INPUT(1, KEYBDINPUT(vk, scan, flags, 0, 0)) for vk, scan, flags in events])
    ctypes.windll.user32.SendInput(len(events), inputs, ctypes.sizeof(INPUT))


def press(*keys):
    """Press keys together, e.g. press(VK_CONTROL, VK_A) = Ctrl+A."""
    _send([(k, 0, 0) for k in keys] + [(k, 0, KEYUP) for k in reversed(keys)])


def type_text(text):
    """Type text as if from the keyboard. New lines use Shift+Enter (plain Enter would send)."""
    for line_number, line in enumerate(text.split("\n")):
        if line_number:
            press(VK_SHIFT, VK_RETURN)
        units = line.encode("utf-16-le")  # emojis are 2 units; each is sent as its own event
        codes = [int.from_bytes(units[i:i + 2], "little") for i in range(0, len(units), 2)]
        _send([e for c in codes for e in ((0, c, UNICODE), (0, c, UNICODE | KEYUP))])
        time.sleep(0.02)


def exact_words(task, request):
    """The user's own words from the task ('send "hi" to mahi' -> "hi"),
    but only if they appear word for word in what the user typed. Otherwise None."""
    match = re.search(r'"([^"]+)"', task)
    if not match:
        return None
    words = match.group(1).strip()
    pattern = r"(?<!\w)" + re.escape(words) + r"(?!\w)"
    found = re.search(pattern, request, re.IGNORECASE)
    return found.group(0) if found else None  # the user's own spelling and capitals


def message_to_send(task, state):
    """The user's exact words; else the Writer's file or Calc's answer from THIS request;
    else the same from the previous request (for "send this answer to ..."). None if nothing."""
    text = exact_words(task, state.get("request", ""))
    if text is not None:
        return text
    for source in (state, state.get("previous") or {}):
        path = source.get("last_file")
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        if source.get("last_answer"):
            return source["last_answer"]
    return None


def run_whatsapp(task, state):
    """Send the user's exact words, or the last message/answer, to a contact named in the request."""
    start = time.time()

    # 1. What to send
    text = message_to_send(task, state)
    if text is None:
        return refuse("there is no message to send. Ask me to write one first.", start)
    if not text:
        return refuse("the message is empty.", start)
    if re.search(r"\[[^\]]+\]", text):
        return refuse("the message still has blanks like [Name]. Fill them in first.", start)
    if len(text) > MAX_LENGTH:
        return refuse(f"the message is longer than {MAX_LENGTH} characters.", start)

    # 2. Who to send to: only a saved contact the USER named (not the model)
    contacts = load_contacts()
    if not contacts:
        return refuse("no contacts saved yet. Add names and numbers to contacts.json "
                      "(see contacts.example.json).", start)
    names = contact_in_request(state.get("request", ""), contacts)
    if not names:  # no name given, or a name that isn't saved
        saved = ", ".join(n.title() for n in contacts)
        return refuse(f"tell me who to send it to (saved contacts: {saved}), e.g. "
                      '"send this answer to mummy on whatsapp". New people go in contacts.json.', start)
    if len(names) > 1:
        # Several people named: this step is for the ONE of them named in its task
        names = [n for n in names if n in contact_in_request(task, contacts)]
        if len(names) != 1:
            return refuse("I couldn't tell which contact this message is for.", start)
    name = names[0]
    number = contacts[name]

    if state.get("dry_run"):
        return make_result("send_whatsapp", name, f"[dry run] Would send on WhatsApp to {name.title()}", start)
    if sys.platform != "win32":
        return refuse("sending on WhatsApp only works on Windows.", start)

    # 3. Open the chat, type the message, press Enter
    # (WhatsApp Desktop ignores the link's "text=" part, so the message is typed instead)
    was_open = whatsapp_is_open()
    link = "whatsapp://send?" + urllib.parse.urlencode({"phone": number})
    os.startfile(link)

    deadline = time.time() + WAIT_FOR_WINDOW
    while not front_window_is_whatsapp():
        if time.time() > deadline:
            return refuse("WhatsApp didn't come to the front, so I didn't type or send anything. "
                          "Try again.", start)
        time.sleep(0.3)
    time.sleep(SETTLE_RUNNING if was_open else SETTLE_STARTING)  # let the chat and text box load

    # A window opened by an earlier step (e.g. Notepad) can finish loading now and
    # jump in front: bring WhatsApp back and check again that it STAYS in front
    for _ in range(FOCUS_TRIES):
        if front_window_is_whatsapp():
            break
        if bring_whatsapp_to_front(link):
            time.sleep(SETTLE_REFOCUS)
    if not front_window_is_whatsapp():  # checked again right before typing
        return refuse("another window kept coming to the front, so I didn't type or send anything.", start)
    # WhatsApp handles keys a little later than we send them, so each step gets a
    # short pause (without them, "select all" landed AFTER the typing and Enter did nothing)
    press(VK_CONTROL, VK_A)   # clear any old draft in the message box
    time.sleep(0.4)
    press(VK_BACK)
    time.sleep(0.4)
    type_text(text)
    time.sleep(0.6)
    if not front_window_is_whatsapp():  # and once more right before Enter
        return refuse("another window came to the front, so I didn't press send.", start)
    press(VK_END)             # cursor to the end: nothing selected, so Enter sends
    time.sleep(0.2)
    press(VK_RETURN)
    return make_result("send_whatsapp", name, f"Sent on WhatsApp to {name.title()}: \"{text[:60]}\"", start)


if __name__ == "__main__":  # self-check: python -m agents.whatsapp
    sample = ("Mahi, +91 98765 43210\nMummy: 919123456789\n+44 7700 900123 John Smith\n"
              "Riya - (91) 99887-76655\n\nno number here\n12345\nRahul\t+91 90000 11111\n")
    got, skipped = parse_contacts_text(sample)
    assert got == {"Mahi": "+919876543210", "Mummy": "919123456789", "John Smith": "+447700900123",
                   "Riya": "919988776655", "Rahul": "+919000011111"}, got
    assert skipped == 2, skipped
    print("ok")
