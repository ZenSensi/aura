"""
agents/organiser.py - The Organiser agent.

Tidies a messy folder (Downloads by default, or Desktop / Documents):
loose files are moved into folders by type, e.g.
    Downloads\\notes.pdf   -> Downloads\\PDFs\\notes.pdf
    Downloads\\setup.exe   -> Downloads\\Installers\\setup.exe
"preview" shows what would move, without moving anything.
"undo" puts the last tidy-up back exactly as it was.

NO AI MODEL: the folder and the mode are read from the words, and the type
of each file comes from its extension, so the result is always the same.

SAFETY:
  - Only MOVES files, inside the same folder. Never deletes, renames
    (except adding " (2)" when a name is taken) or opens anything.
  - Only the files lying directly in the folder; sub-folders are left alone.
  - Skips hidden files, shortcuts, unfinished downloads, files changed in
    the last few minutes, and files of unknown types.
  - Every move is written to logs/organise_undo.json first, so it can be undone.
"""

import json
import os
import re
import time
from datetime import datetime

import config
from agents.files import search_roots

UNDO_FILE = os.path.join(config.LOG_DIR, "organise_undo.json")
FOLDERS = ("downloads", "desktop", "documents")
MAX_MOVES = 2000
RECENT_SECONDS = 180  # files changed this recently may still be downloading or open

# Folder name -> the extensions that go in it
GROUPS = {
    "PDFs": {"pdf"},
    "Images": {"jpg", "jpeg", "png", "gif", "bmp", "webp", "heic", "svg", "tif", "tiff", "ico", "avif"},
    "Videos": {"mp4", "mkv", "mov", "avi", "webm", "wmv", "flv", "m4v", "3gp"},
    "Music": {"mp3", "wav", "m4a", "aac", "flac", "ogg", "wma", "opus"},
    "Documents": {"doc", "docx", "txt", "rtf", "odt", "md", "epub"},
    "Spreadsheets": {"xls", "xlsx", "csv", "ods"},
    "Presentations": {"ppt", "pptx", "odp"},
    "Archives": {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso"},
    "Installers": {"exe", "msi", "msix", "appx", "apk", "dmg"},
    "Code": {"py", "ipynb", "java", "c", "cpp", "h", "js", "ts", "html", "css", "json", "sql", "xml", "yaml", "yml"},
}
GROUP_OF = {ext: group for group, exts in GROUPS.items() for ext in exts}
SKIP_EXTS = {"lnk", "url", "crdownload", "part", "partial", "tmp", "download", "ini", "opdownload"}


def read_task(task):
    """'tidy my desktop' -> ('desktop', 'organise'); 'undo the tidy-up' -> (None, 'undo')."""
    text = task.lower()
    if re.search(r"\b(undo|put (them|it|everything|the files) back|restore|revert)\b", text):
        return None, "undo"
    folder = next((f for f in FOLDERS if f[:-1] in text), "downloads")  # "download", "desktop", "document"
    mode = "preview" if re.search(r"\b(preview|what would|show me what|dry run|without moving)\b", text) else "organise"
    return folder, mode


def folder_path(folder):
    """The real folder, e.g. C:\\Users\\you\\Downloads (the first one that exists)."""
    found = [p for p in search_roots(folder) if os.path.isdir(p)]
    return found[0] if found else None


def plan_moves(root, now=None):
    """[(from, to, group)] for the loose files in root that have a known type."""
    now = now or time.time()
    moves, taken = [], set()
    for entry in sorted(os.scandir(root), key=lambda e: e.name.lower()):
        if not entry.is_file(follow_symlinks=False) or entry.name.startswith((".", "~$")):
            continue
        ext = entry.name.rsplit(".", 1)[-1].lower() if "." in entry.name else ""
        group = GROUP_OF.get(ext)
        if not group or ext in SKIP_EXTS:
            continue
        try:
            info = entry.stat()
            hidden = getattr(info, "st_file_attributes", 0) & 0x6  # Windows hidden / system
        except OSError:
            continue
        if hidden or now - info.st_mtime < RECENT_SECONDS:
            continue
        target = free_name(os.path.join(root, group), entry.name, taken)
        taken.add(target.lower())
        moves.append((entry.path, target, group))
        if len(moves) == MAX_MOVES:
            break
    return moves


def free_name(folder, name, taken):
    """folder\\name, or folder\\name (2).ext if that is already used."""
    stem, ext = os.path.splitext(name)
    path, n = os.path.join(folder, name), 2
    while os.path.exists(path) or path.lower() in taken:
        path = os.path.join(folder, f"{stem} ({n}){ext}")
        n += 1
    return path


def group_counts(moves):
    counts = {}
    for _, _, group in moves:
        counts[group] = counts.get(group, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def organise(root, label):
    """Move the files and write the undo list. Returns (moved, skipped)."""
    moves = plan_moves(root)
    done, skipped = [], 0
    created = [os.path.join(root, g) for g, _ in group_counts(moves) if not os.path.isdir(os.path.join(root, g))]
    try:
        for source, target, group in moves:
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                if os.path.exists(target):  # appeared meanwhile: never overwrite
                    skipped += 1
                    continue
                os.rename(source, target)  # same folder tree = a move, never a copy + delete
                done.append((source, target, group))
            except OSError:  # e.g. the file is open in another program
                skipped += 1
    finally:
        if done:  # a run that moved nothing keeps the last undo list
            with open(UNDO_FILE, "w", encoding="utf-8") as f:
                json.dump({"folder": label, "time": datetime.now().isoformat(timespec="seconds"),
                           "moves": [[s, t] for s, t, _ in done], "created": created}, f, indent=1)
    return done, skipped


def undo():
    """Put the last tidy-up back. Returns (moved back, could not, folder label)."""
    try:
        with open(UNDO_FILE, encoding="utf-8") as f:
            last = json.load(f)
    except (OSError, ValueError):
        return 0, 0, None
    back = failed = 0
    for source, target in reversed(last.get("moves", [])):
        if os.path.isfile(target) and not os.path.exists(source):
            try:
                os.rename(target, source)
                back += 1
                continue
            except OSError:
                pass
        failed += 1
    for folder in last.get("created", []):  # the folders the tidy-up made, only if now empty
        try:
            os.rmdir(folder)
        except OSError:
            pass
    os.remove(UNDO_FILE)  # one undo per tidy-up
    return back, failed, last.get("folder")


def run_organiser(task, state):
    """Run the Organiser agent on one task, e.g. "organise my downloads folder"."""
    start = time.time()
    folder, mode = read_task(task)
    if state.get("dry_run") and mode == "organise":
        mode = "preview"  # tests never move real files
    groups = []

    if mode == "undo":
        back, failed, label = undo()
        if label is None:
            action, message = "refuse", "There is no tidy-up to undo."
        else:
            action = "undo"
            message = f"Put {back} file{'s' if back != 1 else ''} back in {label}."
            if failed:
                message += f" {failed} had been moved or changed since, so I left them."
    else:
        root = folder_path(folder)
        label = folder.title()
        if not root:
            return result(start, "refuse", label, f"I couldn't find your {label} folder.", [])
        if mode == "preview":
            moves = plan_moves(root)
            groups = group_counts(moves)
            action = "preview"
            message = (f"{label}: {len(moves)} file{'s' if len(moves) != 1 else ''} would move into "
                       f"{len(groups)} folder{'s' if len(groups) != 1 else ''}. Nothing was moved yet."
                       if moves else f"{label} is already tidy: no loose files to sort.")
        else:
            done, skipped = organise(root, label)
            groups = group_counts(done)
            action = "organise"
            if done:
                message = (f"Tidied {label}: moved {len(done)} file{'s' if len(done) != 1 else ''} into "
                           f"{len(groups)} folder{'s' if len(groups) != 1 else ''}. Nothing was deleted; "
                           f"say \"undo the tidy-up\" to put them back.")
            else:
                message = f"{label} is already tidy: no loose files to sort."
            if skipped:
                message += f" {skipped} file{'s were' if skipped != 1 else ' was'} in use, so I left {'them' if skipped != 1 else 'it'}."
    return result(start, action, folder or "", message, groups)


def result(start, action, target, message, groups):
    return {
        "agent": "organiser", "model": "none (rules)", "seconds": round(time.time() - start, 2),
        "fallback_used": False, "action": {"action": action, "target": target, "reason": message},
        "message": message, "groups": [{"folder": g, "count": n} for g, n in groups],
    }
