"""
agents/system.py - The System agent.

Opens apps, websites, or the last file the Writer made.

SAFETY DESIGN:
  The model only CHOOSES an action and a name from a list.
  Python then checks that choice and looks up how to open it.
  The model never writes a command, so it cannot run anything
  that is not on the list. No shell is ever used.

  Apps = the fixed apps in config.py + every app INSTALLED on this PC
  (read by Python from the Start Menu), minus apps that can run
  commands or change the system (config.BLOCKED_APP_WORDS).
"""

import difflib
import json
import os
import platform
import re
import subprocess
import threading
import webbrowser

import config
from agents.files import RUNNABLE  # file types that are programs/scripts: never opened
from llm import call_llm_json

ACTIONS = ("open_app", "open_site", "open_last_file", "refuse")


# ---------------------------------------------------------------
# Installed apps (read once, then kept in memory)
# ---------------------------------------------------------------
_installed = None
_installed_lock = threading.Lock()


def read_installed():
    """List (name, how to launch it) of apps installed on this computer."""
    os_name = current_os()
    if os_name == "windows":
        # Get-StartApps lists every app in the Start Menu (normal and Store apps).
        # This is a FIXED command; nothing from the model goes into it.
        command = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                   "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", command],
                             capture_output=True, encoding="utf-8", timeout=30,
                             creationflags=subprocess.CREATE_NO_WINDOW)
        apps = json.loads(out.stdout or "[]")
        apps = [apps] if isinstance(apps, dict) else apps
        return [(a["Name"], ["explorer.exe", "shell:AppsFolder\\" + a["AppID"]]) for a in apps]
    if os_name == "mac":
        return [(f[:-4], ["open", "-a", f[:-4]]) for f in os.listdir("/Applications") if f.endswith(".app")]
    return []  # Linux: only the fixed apps in config.py


def installed_apps():
    """{lower-case name: {"name", "launch"}} of installed apps that are NOT blocked."""
    global _installed
    with _installed_lock:
        if _installed is None:
            _installed = {}
            try:
                found = read_installed()
            except (OSError, ValueError, subprocess.SubprocessError):
                found = []
            for name, launch in found:
                low = name.strip().lower()
                blocked = any(word in low for word in config.BLOCKED_APP_WORDS)
                blocked = blocked or bool(re.search(r"\.(txt|pdf|html?|url|chm|rtf|md)$", low))  # files, not apps
                if low and not blocked and low not in config.ALLOWED_APPS:
                    _installed[low] = {"name": name.strip(), "launch": launch}
        return _installed


def find_installed(target):
    """Match the model's app name to an installed app, or return None."""
    apps = installed_apps()
    if target in apps:
        return target
    # e.g. "vs code" -> "visual studio code": only accept a clear, single match
    close = difflib.get_close_matches(target, apps.keys(), n=1, cutoff=0.85)
    if close:
        return close[0]
    contains = [name for name in apps if target and target in name]
    if len(contains) == 1:
        return contains[0]
    # Initials / word starts, e.g. "vs" or "vs code" -> "visual studio code".
    # If several apps fit, take the shortest name (the most direct match).
    def fits(name):
        words = [w for w in re.split(r"[^a-z0-9]+", name) if w]
        initials = "".join(w[0] for w in words)
        return all(any(w.startswith(t) for w in words) or initials.startswith(t) for t in target.split())
    candidates = sorted((name for name in apps if target and fits(name)), key=len)
    return candidates[0] if candidates else None


def build_prompt():
    """Build the system prompt, listing exactly what is allowed."""
    apps = ", ".join(config.ALLOWED_APPS.keys())
    installed = "; ".join(sorted(a["name"] for a in installed_apps().values()))
    sites = ", ".join(config.ALLOWED_SITES.keys())
    return f"""You are the System agent of a desktop assistant.
You can only open things from these lists:
- Apps:  {apps}
- Installed apps: {installed}
- Sites: {sites}
- The last file: the one the Writer agent saved, or the one the Files agent just found (action "open_last_file").

Pick ONE action:
- "open_app"       -> target must be one of the apps or installed apps above, written exactly as listed
                      (e.g. "open vs code" -> "Visual Studio Code")
- "open_site"      -> target must be one of the sites above
- "open_last_file" -> target is ""
- "refuse"         -> anything else (not in the lists, deleting files, running commands, etc.)

Reply with ONLY this JSON, nothing else:
{{"action": "open_app" | "open_site" | "open_last_file" | "refuse", "target": "name or empty", "reason": "one short sentence"}}"""


def current_os():
    """Return 'windows', 'mac' or 'linux'."""
    name = platform.system()
    if name == "Windows":
        return "windows"
    if name == "Darwin":
        return "mac"
    return "linux"


def validate_action(data):
    """
    Check the model's choice against the allowlist.
    Anything not allowed is turned into a 'refuse' action.
    """
    action = str(data.get("action", "")).strip()
    target = str(data.get("target", "")).strip().lower()
    reason = str(data.get("reason", "")).strip()

    if action not in ACTIONS:
        return {"action": "refuse", "target": "", "reason": f"Unknown action '{action}'."}
    if action == "open_app" and target not in config.ALLOWED_APPS:
        match = find_installed(target)
        if not match:
            return {"action": "refuse", "target": target,
                    "reason": f"'{target}' is not installed, or it is blocked for safety."}
        target = match
    if action == "open_site" and target not in config.ALLOWED_SITES:
        return {"action": "refuse", "target": target, "reason": f"'{target}' is not in the site allowlist."}
    return {"action": action, "target": target, "reason": reason}


def open_file(path):
    """Open a file with the default program on Windows, Mac or Linux."""
    os_name = current_os()
    if os_name == "windows":
        os.startfile(path)                      # Windows built-in
    elif os_name == "mac":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def execute_action(action, state):
    """Carry out a validated action. Returns a short message for the user."""
    kind, target = action["action"], action["target"]

    if kind == "refuse":
        return "Refused: " + (action["reason"] or "not allowed.")

    # Test mode: report what WOULD happen, but do not open anything
    if state.get("dry_run"):
        return f"[dry run] Would {kind.replace('_', ' ')}: {target or 'last file'}"

    if kind == "open_app":
        if target in config.ALLOWED_APPS:
            subprocess.Popen(config.ALLOWED_APPS[target][current_os()])  # list form, no shell
            return f"Opened app: {target}"
        app = installed_apps()[target]
        subprocess.Popen(app["launch"])         # list form, no shell
        return f"Opened app: {app['name']}"

    if kind == "open_site":
        webbrowser.open(config.ALLOWED_SITES[target])
        return f"Opened site: {target}"

    if kind == "open_last_file" and state.get("last_source") == "files":
        # The Files agent found it just now, e.g. "find my resume and open it".
        # Its paths come from Python's own search of your folders, never from a model.
        found = [p for p in state.get("found_files") or [] if os.path.isfile(p)]
        if not found:
            return "Refused: the file search found nothing to open."
        path = found[0]  # newest
        if os.path.splitext(path)[1].lower() in RUNNABLE:
            return "Refused: programs and scripts are never opened, only shown."
        open_file(path)
        more = f" (the newest of {len(found)} found)" if len(found) > 1 else ""
        return f"Opened file: {os.path.basename(path)}{more}"

    if kind == "open_last_file":
        path = state.get("last_file")
        if not path or not os.path.isfile(path):
            return "Refused: no file has been written yet."
        # Only open files inside our own outputs/ folder
        if os.path.dirname(os.path.abspath(path)) != os.path.abspath(config.OUTPUT_DIR):
            return "Refused: file is outside the outputs folder."
        open_file(path)
        return f"Opened file: {os.path.basename(path)}"


def run_system(task, state):
    """
    Run the System agent on one task, e.g. "open notepad".
    1) Ask the model to choose an action   2) validate it   3) execute it
    """
    reply = call_llm_json("system", build_prompt(), task)
    action = validate_action(reply["data"])

    # Second check for short names the model did not recognise ("open vs" = VS Code):
    # if it refused "open <name>" but <name> clearly matches an installed app, open that.
    asked = re.match(r"^\s*(?:please\s+)?(?:open|launch|start|run)\s+(?:the\s+)?(.+?)(?:\s+app)?\s*$", task, re.I)
    if action["action"] == "refuse" and asked:
        match = find_installed(asked.group(1).strip().lower())
        if match:
            action = {"action": "open_app", "target": match, "reason": "matched an installed app by name"}

    # "open the file "wsc logo"" right after the Files agent found it = open that file
    if (action["action"] == "refuse" and state.get("last_source") == "files"
            and re.search(r"\b(open|show|launch|view)\b", task, re.I) and re.search(r"\b(file|it|that|this)\b", task, re.I)):
        action = {"action": "open_last_file", "target": "", "reason": "the file the Files agent just found"}

    message = execute_action(action, state)
    if message.startswith("Refused:"):  # e.g. nothing to open: show it as refused, not "Done"
        action = {**action, "action": "refuse", "reason": message.removeprefix("Refused: ")}

    return {
        "agent": "system",
        "model": reply["model"],
        "seconds": reply["seconds"],
        "fallback_used": reply["fallback_used"],
        "action": action,
        "message": message,
    }
