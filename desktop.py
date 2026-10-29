"""
desktop.py - Aura as a desktop app (its own window, no browser).

Run:  python desktop.py

How it works:
  - pywebview opens a native window and shows static/index.html inside it
    (on Windows it uses the Edge WebView2 engine built into Windows).
  - The Api class below is given to the window as "js_api". Every public
    method becomes callable from JavaScript, e.g.
        await window.pywebview.api.run("open notepad")
  - While run() is working, each orchestrator event is pushed to the page
    with window.evaluate_js("onAgentEvent({...})"), so the UI updates live.
  - No web server, no browser, no open port: JavaScript talks to Python directly.
  - When the window is minimised, the dock (dock.py) appears at the top of the
    screen, so you can keep giving Aura tasks.
  - The Scheduler (automations.py) runs routines and shows reminders while Aura
    is open. "Start with Windows" opens Aura minimised when you sign in.
"""

import ctypes
import json
import os
import re
import shutil
import subprocess
import webbrowser
import sys
import threading
from datetime import datetime, timedelta

import webview

import automations
import config
import google_sync
import voice
from agents.system import open_file, installed_apps
from logger import recent, usage
from orchestrator import run_request, agents_off, set_agent_on
from agents.inbox import find_contact_name
from agents.whatsapp import load_contacts, parse_contacts_text
from agents.files import RUNNABLE, search_roots
from agents.reader import FILE_TYPES as READABLE, kind_of as file_kind
from dock import Dock, WIDTH as DOCK_WIDTH, HEIGHT as DOCK_HEIGHT
from notify import Notifier

MAX_REQUEST_CHARS = 1000
STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"  # this user's "start when I sign in" list
STARTUP_NAME = "Aura"
SNOOZE_MINUTES = 10
# .env names the Settings panel may change, with a readable name for errors
SETTINGS = {
    "GMAIL_ADDRESS": "Gmail address", "GMAIL_APP_PASSWORD": "App password", "USER_NAME": "Your name",
    "GEMINI_API_KEY": "Gemini key", "GROQ_API_KEY": "Groq key", "GROQ_API_KEY_SYSTEM": "Second Groq key",
    "OPENROUTER_API_KEY": "OpenRouter key", "TAVILY_API_KEY": "Tavily key", "NOTION_TOKEN": "Notion secret",
}

# Title bar colours (background, text) for each theme, so the Windows title bar
# blends into the app instead of using the Windows accent colour.
TITLE_BAR = {"dark": ("#090b17", "#c9cde3"), "light": ("#eef0fa", "#171a26")}


def style_title_bar(window, theme):
    """
    Colour the window's own title bar with Windows' DWM (Windows 11):
    dark mode for the buttons, plus background, text and border colours.
    The normal Windows title bar is kept, so moving, snapping and the
    minimise / maximise / close buttons keep working as usual.
    """
    native = getattr(window, "native", None)
    if sys.platform != "win32" or native is None:
        return
    background, text = TITLE_BAR.get(theme, TITLE_BAR["dark"])
    hwnd = int(native.Handle.ToInt64())
    dwm = ctypes.windll.dwmapi

    def colorref(hex_color):  # "#rrggbb" -> 0x00bbggrr (Windows colour format)
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
        return ctypes.c_uint(r | g << 8 | b << 16)

    def set_attribute(number, value):
        dwm.DwmSetWindowAttribute(hwnd, number, ctypes.byref(value), ctypes.sizeof(value))

    set_attribute(20, ctypes.c_int(1 if theme == "dark" else 0))  # DWMWA_USE_IMMERSIVE_DARK_MODE
    set_attribute(35, colorref(background))                       # DWMWA_CAPTION_COLOR
    set_attribute(36, colorref(text))                             # DWMWA_TEXT_COLOR
    set_attribute(34, colorref(background))                       # DWMWA_BORDER_COLOR


def resource_path(*parts):
    """Find bundled files (static/) both in normal runs and inside the .exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def output_path(name):
    """Turn a file name from the page into a safe path inside outputs/ (or None)."""
    name = os.path.basename(str(name or ""))  # strips folders like ../../
    path = os.path.join(config.OUTPUT_DIR, name)
    return path if name and os.path.isfile(path) else None


def notification(request, events):
    """What the "task done" card says, from one request's events: (kind, title, text)."""
    steps = [e for e in events if e["type"] == "step_done"]
    stopped = next((e for e in events if e["type"] in ("step_failed", "error")), None)

    def says(event):  # one step's result in a short line
        result = event["result"]
        if event["agent"] == "writer":
            return f"Wrote {'an' if result['type'] == 'email' else 'a'} {result['type']}: {result['file']}"
        return re.sub(r"\s*\[\d+\]", "", result.get("message") or "")  # web answers: no [1] [2]

    if stopped:
        return "error", request, "Stopped: " + stopped["message"]
    if events and events[-1].get("nothing_to_do"):
        return "info", request, (events[-1].get("reply") or
                                 "I couldn't do that one. Try rephrasing it, or one of the suggestions.")
    refused = [e for e in steps if e["result"].get("action", {}).get("action") == "refuse"]
    if refused:
        return "warn", request, says(refused[-1])
    done = f"{len(steps)} steps done · " if len(steps) > 1 else ""
    return "ok", request, done + (says(steps[-1]) if steps else "All done!")


class Api:
    """Python functions the page can call. Names starting with _ stay hidden."""

    def __init__(self):
        self._window = None
        self._dock = None
        self._notifier = None
        self._lock = threading.Lock()  # one request at a time
        self._memory = {}  # the previous request's answer/file, for follow-ups like "send this answer"
        self._attachment = None  # file picked with the + button (a real path; the page only sees its name)
        self._links = set()  # source links from web searches, which open_link may open
        self._reminders = {}  # reminders shown as a notification, by id, for the Snooze button

    def get_info(self):
        """Agents, models and allowlists for the sidebar."""
        agents = {
            name: {"model": s["model"], "fallback": s.get("fallback", {}).get("model")}
            for name, s in config.AGENT_MODELS.items()
        }
        # First name for the welcome greeting: USER_NAME from .env, else the Windows account name
        names = (config.USER_NAME or os.environ.get("USERNAME", "")).split()
        first_name = names[0].capitalize() if names else ""
        return {"agents": agents, "apps": list(config.ALLOWED_APPS), "sites": list(config.ALLOWED_SITES),
                "sender": config.GMAIL_ADDRESS, "installed_count": len(installed_apps()),
                "user": first_name, "off": sorted(agents_off()), "usage": usage()}

    def get_usage(self):
        """Each agent's request count and last use, refreshed after every request."""
        return usage()

    def set_agent(self, name, on):
        """The light on an agent's row: switch that agent on or off. Returns the switched-off agents."""
        return sorted(set_agent_on(str(name), bool(on)))

    def new_chat(self):
        """The New chat button: forget this conversation (the Chat agent starts fresh)."""
        self._memory.clear()

    def get_recent(self):
        """Your latest requests, for "Recent tasks" in the sidebar."""
        return recent()

    def set_theme(self, theme):
        """The page switched light/dark: colour the title bar to match."""
        style_title_bar(self._window, "light" if theme == "light" else "dark")

    def run(self, text, options=None):
        """
        Run one request. Events are pushed to the page live as they happen.
        options (from the + menu): {"web": True} = Web search on,
        {"attach": True} = use the file picked with pick_attachment().
        {"routine": "Morning news"} = a routine started it (the Scheduler), so it may
        not save new automations, and the notification says which routine it was.
        {"agent": "music"} = the user picked that agent in the sidebar, so only it runs.
        """
        options = options if isinstance(options, dict) else {}
        attachment = self._attachment if options.get("attach") else None
        text = str(text or "").strip()[:MAX_REQUEST_CHARS]
        if not text and attachment:
            text = "Summarise this file"
        if not text:
            return {"ok": False}
        if not self._lock.acquire(blocking=False):
            return {"ok": False, "busy": True}
        if attachment:
            self._attachment = None  # one file per message, like other chat apps
        events = []
        try:
            routine = str(options.get("routine") or "")[:60]
            for event in run_request(text, memory=self._memory, attachment=attachment,
                                     web=bool(options.get("web")), automated=bool(routine),
                                     agent=str(options.get("agent") or "") or None):
                # Only give the page the file NAME, not the full path
                if event["type"] == "step_done" and event["agent"] == "writer":
                    result = dict(event["result"], file=os.path.basename(event["result"]["file"]))
                    event = dict(event, result=result)
                if event["type"] == "step_done" and event["agent"] in ("web", "notion"):
                    self._links.update(source["url"] for source in event["result"]["sources"])
                events.append(event)
                self._push(self._window, f"onAgentEvent({json.dumps(event)})")
                if self._dock and self._dock.visible:  # the dock shows the same steps
                    self._push(self._dock.window, f"onDockEvent({json.dumps(event)})")
        finally:
            self._lock.release()
        if self._notifier:  # the "task done" card at the top-right of the screen
            kind, title, says = notification(text, events)
            self._notifier.notify(kind, f"Routine · {routine}" if routine else title, says)
        return {"ok": True}

    @staticmethod
    def _push(window, script):
        """Send one event to a page. A display error there must not stop the agents."""
        try:
            window.evaluate_js(script)
        except Exception as e:  # e.g. a JavaScript error while drawing
            print(f"  [ui] could not show event: {e}")

    def open_file(self, name):
        """Open a written file with the default program (e.g. Notepad)."""
        path = output_path(name)
        if not path:
            return False
        open_file(path)
        return True

    def save_file(self, name):
        """Show a 'Save as' dialog and copy the written file there."""
        path = output_path(name)
        if not path:
            return False
        choice = self._window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename=os.path.basename(path),
            file_types=("Text files (*.txt)", "All files (*.*)"),
        )
        if not choice:
            return False  # user cancelled
        destination = choice if isinstance(choice, str) else choice[0]
        shutil.copyfile(path, destination)
        return True

    def get_apps(self):
        """Names of every app Aura may open, for the suggestions in the 'Open an app' form."""
        fixed = [name.title() for name in config.ALLOWED_APPS]
        return sorted(set(fixed) | {a["name"] for a in installed_apps().values()}, key=str.lower)

    def lookup_name(self, address):
        """Name of an email address, from your Gmail or guessed (for the forms)."""
        return find_contact_name(str(address or "")[:200])

    def _found_file(self, path):
        """Only files inside the searched folders, that still exist, may be opened."""
        path = os.path.abspath(str(path or ""))
        inside = any(path.startswith(root + os.sep) for root in search_roots("all"))
        return path if inside and os.path.isfile(path) else None

    def open_found(self, path):
        """'Open' button on a Files result. Programs and scripts are never run."""
        path = self._found_file(path)
        if not path or os.path.splitext(path)[1].lower() in RUNNABLE:
            return False
        open_file(path)
        return True

    def show_found(self, path):
        """'Show in folder' button: opens File Explorer with the file selected."""
        path = self._found_file(path)
        if not path:
            return False
        subprocess.Popen(["explorer.exe", "/select,", path])  # list form, no shell
        return True

    def open_link(self, url):
        """Open a news headline or a web search source in the normal web browser."""
        url = str(url or "")
        if not (url.startswith("https://news.google.com/") or url in self._links):
            return False  # only links Aura itself showed
        webbrowser.open(url)
        return True

    # ---------- Attachments (the + button in the chat box) ----------
    def pick_attachment(self):
        """Windows' own 'Open' dialog. Returns the file's name, type and size, or None."""
        choice = self._window.create_file_dialog(
            webview.OPEN_DIALOG,
            file_types=(
                # pywebview allows only letters and spaces in the name (no commas)
                "Images PDFs and documents (" + ";".join("*" + ext for ext in sorted(READABLE)) + ")",
                "All files (*.*)",
            ),
        )
        if not choice:
            return None  # cancelled
        path = choice if isinstance(choice, str) else choice[0]
        if not file_kind(path):
            return {"error": "I can read PDFs, pictures, Word (.docx) and text files, but not that kind of file."}
        size = os.path.getsize(path)
        if size > config.MAX_ATTACHMENT_MB * 1024 * 1024:
            return {"error": f"That file is too big (the limit is {config.MAX_ATTACHMENT_MB} MB)."}
        self._attachment = path
        return {"name": os.path.basename(path), "kind": file_kind(path),
                "size": f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.1f} MB"}

    def clear_attachment(self):
        """The × on the attachment chip."""
        self._attachment = None

    # ---------- Voice typing (voice.py) ----------
    def voice_start(self):
        """Mic button pressed: start recording."""
        try:
            voice.start()
            return {"ok": True}
        except voice.VoiceError as e:
            return {"ok": False, "error": str(e)}

    def voice_stop(self):
        """Mic button pressed again: stop and return the spoken text."""
        try:
            return {"ok": True, "text": voice.stop()}
        except voice.VoiceError as e:
            return {"ok": False, "error": str(e)}

    def voice_cancel(self):
        voice.cancel()

    # ---------- Dock (the floating pill shown while Aura is minimised) ----------
    def dock_expand(self):
        self._dock.expand()

    def dock_collapse(self):
        self._dock.collapse()

    def dock_shape(self, width, height, radius):
        """The dock page is animating to this size (page pixels, ring included)."""
        try:
            width, height, radius = (int(float(v)) for v in (width, height, radius))
        except (TypeError, ValueError):
            return
        self._dock.set_shape(max(40, min(DOCK_WIDTH, width)), max(30, min(DOCK_HEIGHT, height)),
                             max(0, min(height // 2, radius)))

    def dock_run(self, text):
        """A task typed in the dock: the main window runs it (so it is also in the chat)."""
        text = str(text or "").strip()[:MAX_REQUEST_CHARS]
        if not text or self._lock.locked():
            return False
        self._window.run_js(f"send({json.dumps(text)})")
        return True

    def dock_media(self, command):
        """Now Playing buttons in the dock: 'toggle' | 'next' | 'previous' (nowplaying.py checks the word)."""
        return self._dock.media.press(str(command))

    def dock_open_main(self):
        """'Open Aura' in the dock: bring the main window back."""
        self._window.restore()
        self._window.show()

    # ---------- "Task done" notification (notify.py) ----------
    def notify_shape(self, height):
        """The notification page measured its card: cut the window to it."""
        try:
            self._notifier.cut(max(0, min(400, int(float(height)))))
        except (TypeError, ValueError):
            pass

    def notify_hide(self):
        self._notifier.hide()

    def notify_open(self):
        """The notification was clicked: bring Aura's window to the front."""
        self._window.restore()
        self._window.show()

    def notify_snooze(self, reminder_id):
        """'Snooze' on a reminder notification: show it again in SNOOZE_MINUTES."""
        reminder = self._reminders.pop(str(reminder_id), None)
        if reminder:
            moment = datetime.now().replace(microsecond=0) + timedelta(minutes=SNOOZE_MINUTES)
            automations.add_reminder(reminder["text"], moment)
            self._refresh_automations()

    # ---------- Automations (automations.py): routines, reminders, workflows ----------
    def get_automations(self):
        """Everything saved, with readable times, for the Automations panel."""
        return {**automations.summary(), "startup": self.get_startup(), "can_startup": sys.platform == "win32"}

    def automation_pause(self, item_id, paused):
        return bool(automations.change(str(item_id), paused=bool(paused)))

    def automation_delete(self, item_id):
        return automations.remove(str(item_id))

    def _remind(self, reminder, late=False):
        """The Scheduler: a reminder is due. Show it as a notification with Snooze."""
        self._reminders[reminder["id"]] = reminder
        when = automations.parse_stamp(reminder["at"])
        note = f"Missed at {automations.clock(when)} while Aura was closed" if late and when else "Reminder"
        if self._notifier:
            self._notifier.notify("remind", reminder["text"], note, snooze=reminder["id"])
        self._refresh_automations()

    def _start_routine(self, routine):
        """The Scheduler: a routine is due. It runs in the main window like a typed request.
        Returns False if Aura is busy, so the Scheduler tries again a few seconds later."""
        if self._lock.locked() or not self._window:
            return False
        try:
            started = self._window.evaluate_js(
                f"runRoutine({json.dumps(routine['task'])}, {json.dumps(routine['name'])})")
        except Exception:
            return False
        return bool(started)

    def _refresh_automations(self):
        if self._window:
            self._push(self._window, "window.loadAutomations && loadAutomations()")

    # ---------- Start with Windows ----------
    @staticmethod
    def _startup_command():
        """How Windows should start Aura when you sign in: minimised, so it waits in the dock."""
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}" --minimized'
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        return f'"{pythonw if os.path.isfile(pythonw) else sys.executable}" "{os.path.abspath(__file__)}" --minimized'

    def get_startup(self):
        if sys.platform != "win32":
            return False
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY) as key:
                return winreg.QueryValueEx(key, STARTUP_NAME)[0] == self._startup_command()
        except OSError:
            return False

    def set_startup(self, on):
        """The 'Start Aura when Windows starts' switch (only this user's list; no admin needed)."""
        if sys.platform != "win32":
            return False
        import winreg
        print(f"  [startup] start with Windows -> {'on' if on else 'off'}")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if on:
                winreg.SetValueEx(key, STARTUP_NAME, 0, winreg.REG_SZ, self._startup_command())
            else:
                try:
                    winreg.DeleteValue(key, STARTUP_NAME)
                except FileNotFoundError:
                    pass
        return self.get_startup()

    def open_outputs_folder(self):
        """Open the outputs/ folder in the file manager."""
        open_file(config.OUTPUT_DIR)
        return True

    # ---------- Settings panel ----------
    def get_settings(self):
        """What the Settings panel shows. Keys and passwords never go to the page: only whether they are set."""
        if not google_sync.ready():
            google = "not_setup"
        else:
            google = "connected" if os.path.isfile(google_sync.TOKEN_FILE) else "signed_out"
        try:
            contacts = len(load_contacts())
        except ValueError:
            contacts = -1  # contacts.json is broken
        return {"email": config.GMAIL_ADDRESS, "name": config.USER_NAME,
                "set": {name: bool(os.getenv(name)) for name in SETTINGS},
                "google": google, "contacts": contacts,
                "startup": self.get_startup(), "can_startup": sys.platform == "win32"}

    def save_settings(self, values):
        """Save the changed fields to .env. Only names in SETTINGS; an empty secret keeps the saved one."""
        changes = {}
        for name, value in dict(values or {}).items():
            if name not in SETTINGS:
                continue
            value = str(value).strip()
            if "\n" in value or "\r" in value or len(value) > 300:
                return {"error": f"{SETTINGS[name]} doesn't look right."}
            if name == "GMAIL_APP_PASSWORD":
                value = value.replace(" ", "")
            if name == "GMAIL_ADDRESS" and value and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
                return {"error": "That email address doesn't look right."}
            if value or name in ("GMAIL_ADDRESS", "USER_NAME"):
                changes[name] = value
        if changes:
            print(f"  [settings] saved {', '.join(sorted(changes))}")
            config.save_env(changes)
        return self.get_settings()

    def google_connect(self):
        """Sign in to Google (Tasks + Calendar) in the browser. Waits until it's done."""
        try:
            google_sync._credentials()  # opens the sign-in page if there is no saved sign-in
        except google_sync.GoogleError as e:
            return {"error": str(e)}
        except Exception as e:  # Google libraries missing, no internet...
            return {"error": f"Couldn't connect to Google ({e})."}
        return self.get_settings()

    def google_disconnect(self):
        """Forget the Google sign-in on this PC."""
        try:
            os.remove(google_sync.TOKEN_FILE)
        except FileNotFoundError:
            pass
        return self.get_settings()

    def open_contacts(self):
        """Open contacts.json (WhatsApp contacts) in the default editor."""
        if not os.path.isfile(config.CONTACTS_FILE):
            with open(config.CONTACTS_FILE, "w", encoding="utf-8") as f:
                f.write("{\n}\n")
        open_file(config.CONTACTS_FILE)
        return True

    def import_contacts(self):
        """Settings: pick a .txt of "name, number" lines and add them to contacts.json."""
        choice = self._window.create_file_dialog(webview.OPEN_DIALOG, file_types=("Text files (*.txt)", "All files (*.*)"))
        if not choice:
            return None  # cancelled
        path = choice if isinstance(choice, str) else choice[0]
        if os.path.getsize(path) > 1024 * 1024:
            return {"error": "That file is too big for a contacts list (the limit is 1 MB)."}
        with open(path, encoding="utf-8-sig", errors="replace") as f:
            found, skipped = parse_contacts_text(f.read())
        if not found:
            return {"error": "No contacts found. Put one per line, like: Mahi, +91 98765 43210"}
        try:
            with open(config.CONTACTS_FILE, encoding="utf-8") as f:
                saved = json.load(f)
        except FileNotFoundError:
            saved = {}
        except (OSError, ValueError):
            return {"error": "contacts.json has a mistake: fix it first (Edit contacts), so nothing in it is lost."}
        lower = {str(k).strip().lower(): k for k in saved}
        updated = 0
        for name, number in found.items():
            key = lower.get(name.lower(), name)  # same name (any case) -> replace that number
            updated += key in saved
            saved[key] = number
        with open(config.CONTACTS_FILE, "w", encoding="utf-8") as f:
            json.dump(saved, f, indent=2, ensure_ascii=False)
        return {"added": len(found) - updated, "updated": updated, "skipped": skipped, "settings": self.get_settings()}


def already_running():
    """
    True if Aura is already open (then its window is brought to the front instead).
    Only one Aura may run: two copies would each run every routine and reminder.
    Uses a named Windows "mutex" that lives as long as this Aura is open.
    """
    if sys.platform != "win32":
        return False
    kernel32, user32 = ctypes.windll.kernel32, ctypes.windll.user32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    already_running.mutex = kernel32.CreateMutexW(None, False, "Local\\AuraAssistantSingleInstance")  # kept open
    if kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
        return False
    hwnd = user32.FindWindowW(None, "Aura")
    if hwnd:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    return True


def main():
    if already_running():
        return
    # Read the list of installed apps in the background, so the window opens at once
    threading.Thread(target=installed_apps, daemon=True).start()
    api = Api()
    minimized = "--minimized" in sys.argv  # started with Windows: wait in the dock
    window = webview.create_window(
        "Aura",
        url=resource_path("static", "index.html"),
        js_api=api,
        width=1280, height=840, min_size=(420, 600),
        background_color="#0a0b10",
        minimized=minimized,
    )
    api._window = window
    window.events.shown += lambda: style_title_bar(window, "dark")  # until the page sends its theme

    # The dock: shown when the main window is minimised, hidden when it comes back
    dock = Dock(resource_path("static", "dock.html"), api)
    api._dock = dock
    window.events.minimized += dock.show
    window.events.restored += dock.hide
    window.events.maximized += dock.hide

    # The "task done" notification at the top-right of the screen
    notifier = Notifier(resource_path("static", "notify.html"), api)
    api._notifier = notifier

    def close_helpers():  # closing Aura closes the dock and the notification too
        dock.media.stop()  # and the Now Playing watcher
        dock.window.destroy()
        notifier.window.destroy()
    window.events.closed += close_helpers

    # Routines and reminders: the clock starts once the page can run requests
    scheduler = automations.Scheduler(api._remind, api._start_routine)
    window.events.loaded += scheduler.start
    if minimized:
        window.events.shown += dock.show
    webview.start()


if __name__ == "__main__":
    main()
