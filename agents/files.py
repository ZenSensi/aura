"""
agents/files.py - The Files agent.

Searches your personal folders (Desktop, Documents, Downloads, Pictures,
Music, Videos, and the same folders in OneDrive) for files by name, type
and date, and lists them in the chat.

How it works:
  1) The model turns the task into a small JSON "search plan"
     (e.g. name words "dbms", types ["pdf"], last 7 days).
  2) Python checks the plan and walks the folders itself.

SAFETY:
  - Read-only: it only looks at file NAMES, sizes and dates.
    It never opens, reads, moves or deletes a file.
  - It only searches inside the user's own folders (SEARCH_ROOTS),
    never Windows or Program Files, and skips hidden/system folders.
  - A time limit and a file limit stop it from running forever.
"""

import os
import time
from datetime import datetime

import config
from llm import call_llm_json

HOME = os.path.expanduser("~")
ONEDRIVE = os.environ.get("OneDrive", "")
FOLDER_NAMES = ["Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos"]

MAX_RESULTS = 20       # files shown at most
TIME_LIMIT = 8         # seconds; stop searching after this
MAX_SCANNED = 300_000  # files looked at, at most

# Folders that are never searched (code libraries, caches, system folders)
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "venv", ".venv", "appdata", "$recycle.bin",
             "site-packages", ".cache", "build", "dist"}

# Opening these would RUN a program, so the UI only offers "Show in folder" for them
RUNNABLE = {".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".msi", ".lnk", ".reg", ".scr", ".com", ".jar", ".py"}

PLAN_PROMPT = f"""You are the Files agent of a desktop assistant. You SEARCH for files on the user's computer.
Turn the task into a search plan.

Fields:
- "name": words that must be in the file name (lower case), or "" for any name
- "types": file extensions without dots, e.g. ["pdf"], ["jpg","png"], ["docx","doc"], or [] for any.
  Words like "photos" = ["jpg","jpeg","png","heic"], "videos" = ["mp4","mkv","mov"],
  "documents" = ["pdf","docx","doc","txt","pptx","xlsx"], "songs" = ["mp3","wav","m4a"].
- "folder": one of {", ".join(f.lower() for f in FOLDER_NAMES)}, or "all"
- "days": only files changed in the last N days (e.g. "last week" = 7, "today" = 1), or 0 for any time
- "action": "search", or "refuse" if the task asks to delete, move, rename or change files

Reply with ONLY this JSON, nothing else:
{{"action": "search" | "refuse", "name": "", "types": [], "folder": "all", "days": 0}}"""


def search_roots(folder):
    """The real folders to search, e.g. 'documents' -> Documents and OneDrive\\Documents."""
    names = [f for f in FOLDER_NAMES if f.lower() == folder] or FOLDER_NAMES
    roots = []
    for base in (HOME, ONEDRIVE):
        for name in names:
            path = os.path.join(base, name) if base else ""
            if path and os.path.isdir(path) and path not in roots:
                roots.append(path)
    return roots


def clean_plan(data):
    """Check the model's plan and fill in safe defaults."""
    types = data.get("types") or []
    types = [str(t).lower().strip(". ") for t in types if str(t).strip(". ").isalnum()][:10]
    try:
        days = max(0, min(3650, int(data.get("days") or 0)))
    except (TypeError, ValueError):
        days = 0
    folder = str(data.get("folder") or "all").lower().strip()
    return {
        "action": "refuse" if data.get("action") == "refuse" else "search",
        "words": str(data.get("name") or "").lower().split()[:6],
        "types": types,
        "folder": folder if folder in [f.lower() for f in FOLDER_NAMES] else "all",
        "days": days,
    }


def nice_size(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def walk(plan):
    """Look through the folders and collect matching files. Returns (matches, stopped_early)."""
    oldest = time.time() - plan["days"] * 86400 if plan["days"] else 0
    found, scanned, start = [], 0, time.time()
    stack = search_roots(plan["folder"])

    while stack:
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue  # no permission etc.
        for entry in entries:
            name = entry.name
            if name.startswith(".") or name.startswith("~$"):
                continue  # hidden and temporary files
            try:
                if entry.is_dir(follow_symlinks=False):
                    if name.lower() not in SKIP_DIRS:
                        stack.append(entry.path)
                    continue
                scanned += 1
                low = name.lower()
                ext = os.path.splitext(low)[1].lstrip(".")
                if plan["types"] and ext not in plan["types"]:
                    continue
                if not all(word in low for word in plan["words"]):
                    continue
                info = entry.stat()
                if info.st_mtime < oldest:
                    continue
                found.append((info.st_mtime, entry.path, info.st_size))
            except OSError:
                continue
        if time.time() - start > TIME_LIMIT or scanned > MAX_SCANNED:
            return found, True
    return found, False


def short_folder(path):
    """'C:\\Users\\me\\Documents\\College' -> '~\\Documents\\College' (easier to read)."""
    folder = os.path.dirname(path)
    return "~" + folder[len(HOME):] if folder.startswith(HOME) else folder


def run_files(task, state):
    """Run the Files agent on one task, e.g. "find my pdf files about dbms"."""
    start = time.time()
    reply = call_llm_json("files", PLAN_PROMPT, task)
    plan = clean_plan(reply["data"])
    info = {"agent": "files", "model": reply["model"], "fallback_used": reply["fallback_used"]}

    if plan["action"] == "refuse" or (not plan["words"] and not plan["types"] and not plan["days"]):
        reason = ("I can only search for files, not delete, move or change them."
                  if plan["action"] == "refuse" else "Tell me a file name, a type (like PDF) or a time (like last week).")
        return {**info, "seconds": round(time.time() - start, 2), "files": [],
                "action": {"action": "refuse", "target": ""}, "message": "Refused: " + reason}

    found, stopped = walk(plan)
    found.sort(reverse=True)  # newest first
    files = [{
        "name": os.path.basename(path),
        "folder": short_folder(path),
        "path": path,
        "size": nice_size(size),
        "modified": datetime.fromtimestamp(mtime).strftime("%d %b %Y"),
        "runnable": os.path.splitext(path)[1].lower() in RUNNABLE,
    } for mtime, path, size in found[:MAX_RESULTS]]
    state["found_files"] = [f["path"] for f in files]  # newest first
    if files:
        state["last_source"] = "files"  # "open the last file" = the newest file found (see agents/system.py)

    where = plan["folder"].capitalize() if plan["folder"] != "all" else "your folders"
    if not files:
        message = f"No files found in {where}."
    else:
        more = f" (showing the newest {MAX_RESULTS})" if len(found) > MAX_RESULTS else ""
        message = f"Found {len(found)} file{'s' if len(found) != 1 else ''} in {where}{more}."
    if stopped:
        message += " I stopped early because there are a lot of files; try a more specific search."

    return {**info, "seconds": round(time.time() - start, 2), "files": files,
            "action": {"action": "search", "target": str(len(found))}, "message": message}
