"""
automations.py - Aura's saved automations, and the clock that runs them.

Three kinds, all saved in automations.json next to the app (like contacts.json,
kept only on this PC):

  routines   - an Aura request run on a schedule, e.g. "give me today's news"
               every day at 8:00 AM. It runs through the normal Orchestrator,
               so it can use every agent, and the result shows in the chat
               and as a notification.
  reminders  - a note shown as a notification at a time, with Snooze.
  workflows  - a named request saved for later, e.g. "Study mode" =
               "open VS Code, Spotify and Gmail". Typing "study mode" or
               "run study mode" runs it.

The Automate agent (agents/automate.py) only turns your words into a small
JSON description. build_schedule() below checks it and works out the times
in plain Python, because language models are bad at date arithmetic.

The Scheduler thread checks every few seconds what is due. Automations only
run while Aura is open (it can start with Windows, see desktop.py).

SAFETY:
  - Routines can't run more often than every MIN_EVERY minutes.
  - A routine run can't create more automations (orchestrator.py drops
    Automate steps from automated runs), so nothing can multiply itself.
  - At most MAX_ITEMS of each kind.
"""

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timedelta

import config

FILE = os.path.join(config.BASE_DIR, "automations.json")
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
REPEATS = ("once", "daily", "weekdays", "weekends", "weekly", "hourly", "every")
MIN_EVERY = 15        # minutes: the shortest repeat allowed
MAX_ITEMS = 50        # of each kind
MAX_TASK = 300        # characters of a saved request
LATE_LIMIT = 30       # minutes: a routine missed by more than this (Aura was closed) waits for its next time
TICK = 10             # seconds between checks

_lock = threading.RLock()  # the Scheduler thread and the window both change the file


# ---------------------------------------------------------------
# The file
# ---------------------------------------------------------------
def load():
    """Everything saved: {"routines": [...], "reminders": [...], "workflows": [...]}."""
    with _lock:
        try:
            with open(FILE, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        return {kind: list(data.get(kind) or []) for kind in ("routines", "reminders", "workflows")}


def save(data):
    """Write to a temporary file first, so a crash never leaves a half-written file."""
    with _lock:
        temp = FILE + ".tmp"
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp, FILE)


def new_id():
    return uuid.uuid4().hex[:8]


def stamp(moment):
    return moment.isoformat(timespec="seconds") if moment else None


def parse_stamp(text):
    try:
        return datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


# ---------------------------------------------------------------
# Times
# ---------------------------------------------------------------
class ScheduleError(ValueError):
    """The time the model read from the request makes no sense (shown to the user)."""


def parse_clock(text):
    """'17:00' / '5:30 pm' / '9 PM' -> (hour, minute)."""
    match = re.fullmatch(r"\s*(\d{1,2})(?:[:.](\d{2}))?\s*([ap]\.?m\.?)?\s*", str(text or ""), re.I)
    if not match:
        raise ScheduleError(f"I couldn't read the time '{text}'.")
    hour, minute = int(match.group(1)), int(match.group(2) or 0)
    half = (match.group(3) or "").lower().replace(".", "")
    if half == "pm" and hour < 12:
        hour += 12
    if half == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        raise ScheduleError(f"'{text}' is not a real time.")
    return hour, minute


def resolve_day(day, now):
    """'today' / 'tomorrow' / 'fri' / '2026-10-25' -> a date (or None if no day was said)."""
    day = str(day or "").strip().lower()
    if day in ("", "today"):
        return None if not day else now.date()
    if day == "tomorrow":
        return (now + timedelta(days=1)).date()
    if day[:3] in DAYS:
        ahead = (DAYS.index(day[:3]) - now.weekday()) % 7
        return (now + timedelta(days=ahead)).date()
    try:
        return datetime.strptime(day, "%Y-%m-%d").date()
    except ValueError:
        raise ScheduleError(f"I couldn't read the day '{day}'.")


def one_time(when, now):
    """The moment for a reminder or a one-time routine: in N minutes, or a time (and day)."""
    minutes = when.get("in_minutes")
    if minutes not in (None, "", 0):
        try:
            minutes = float(minutes)
        except (TypeError, ValueError):
            raise ScheduleError("I couldn't read how long to wait.")
        if not 0 < minutes <= 60 * 24 * 365:
            raise ScheduleError("That is too far away, or already passed.")
        return (now + timedelta(minutes=minutes)).replace(microsecond=0)
    if not when.get("time"):
        raise ScheduleError("Please say when, e.g. \"at 5 PM\" or \"in 20 minutes\".")
    hour, minute = parse_clock(when["time"])
    said = str(when.get("day") or "").strip().lower()
    day = resolve_day(said, now)
    moment = datetime.combine(day or now.date(), datetime.min.time()).replace(hour=hour, minute=minute)
    if moment <= now:
        if day is None:
            moment += timedelta(days=1)   # "at 9" when it's already 10 = tomorrow at 9
        elif said[:3] in DAYS:
            moment += timedelta(days=7)   # "on friday at 9" said on friday at 10 = next friday
        else:
            raise ScheduleError("That time has already passed.")
    return moment


def build_schedule(when, now=None):
    """
    Check the model's "when" for a routine and return a clean schedule:
      {"repeat": "daily" | "weekdays" | "weekends" | "weekly", "time": "HH:MM", "days": [...]}
      {"repeat": "hourly", "minute": 30}
      {"repeat": "every", "minutes": 45}
      {"repeat": "once", "at": "2026-10-18T21:00:00"}
    """
    now = now or datetime.now()
    when = when if isinstance(when, dict) else {}
    repeat = str(when.get("repeat") or "once").strip().lower()
    if repeat not in REPEATS:
        raise ScheduleError(f"I can't repeat '{repeat}'.")
    if repeat == "once":
        return {"repeat": "once", "at": stamp(one_time(when, now))}
    if repeat == "every":
        try:
            minutes = int(float(when.get("minutes") or 0))
        except (TypeError, ValueError):
            minutes = 0
        if minutes < MIN_EVERY:
            raise ScheduleError(f"Routines can repeat at most every {MIN_EVERY} minutes.")
        return {"repeat": "every", "minutes": min(minutes, 60 * 24 * 7)}
    if repeat == "hourly":
        minute = int(when.get("minute") or 0) if str(when.get("minute") or "0").isdigit() else 0
        return {"repeat": "hourly", "minute": min(minute, 59)}
    hour, minute = parse_clock(when.get("time") or "9:00")
    days = {"daily": DAYS, "weekdays": DAYS[:5], "weekends": DAYS[5:]}.get(repeat)
    if repeat == "weekly":
        days = [d for d in DAYS if d in {str(x).strip().lower()[:3] for x in when.get("days") or []}]
        if not days:
            raise ScheduleError("Please say which day of the week, e.g. \"every Monday\".")
    return {"repeat": repeat, "time": f"{hour:02d}:{minute:02d}", "days": list(days)}


def next_time(schedule, after):
    """The first moment the schedule is due AFTER the given time (None = it will never run again)."""
    repeat = schedule.get("repeat")
    if repeat == "once":
        moment = parse_stamp(schedule.get("at"))
        return moment if moment and moment > after else None
    if repeat == "every":
        return (after + timedelta(minutes=schedule["minutes"])).replace(microsecond=0)
    if repeat == "hourly":
        moment = after.replace(minute=schedule["minute"], second=0, microsecond=0)
        return moment if moment > after else moment + timedelta(hours=1)
    hour, minute = (int(x) for x in schedule["time"].split(":"))
    for ahead in range(8):
        moment = (after + timedelta(days=ahead)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if moment > after and DAYS[moment.weekday()] in schedule["days"]:
            return moment
    return None


def clock(moment):
    """'8:00 AM'"""
    return moment.strftime("%I:%M %p").lstrip("0")


def when_text(moment, now=None):
    """'today at 5:00 PM' / 'tomorrow at 8:00 AM' / 'Fri 24 Oct at 9:00 PM'"""
    now = now or datetime.now()
    days = (moment.date() - now.date()).days
    day = "today" if days == 0 else "tomorrow" if days == 1 else moment.strftime("%a %d %b").replace(" 0", " ")
    return f"{day} at {clock(moment)}"


def describe(schedule):
    """'Every day at 8:00 AM', 'Weekdays at 9:00 AM', 'Every Mon, Thu at 6:00 PM', 'Every 30 minutes'..."""
    repeat = schedule.get("repeat")
    if repeat == "once":
        moment = parse_stamp(schedule.get("at"))
        return "Once, " + when_text(moment) if moment else "Once"
    if repeat == "every":
        minutes = schedule["minutes"]
        return f"Every {minutes} minutes" if minutes % 60 else f"Every {minutes // 60} hour{'s' if minutes > 60 else ''}"
    if repeat == "hourly":
        return f"Every hour at :{schedule['minute']:02d}"
    at = clock(datetime.strptime(schedule["time"], "%H:%M"))
    if repeat == "daily":
        return f"Every day at {at}"
    if repeat == "weekdays":
        return f"Weekdays at {at}"
    if repeat == "weekends":
        return f"Weekends at {at}"
    return "Every " + ", ".join(d.title() for d in schedule["days"]) + f" at {at}"


# ---------------------------------------------------------------
# Adding, finding and changing
# ---------------------------------------------------------------
def clean_name(name, fallback):
    name = " ".join(str(name or "").split())[:40].strip(" .\"'")
    return name or " ".join(str(fallback).split()[:4])[:40] or "Untitled"


def check_room(data, kind):
    if len(data[kind]) >= MAX_ITEMS:
        raise ScheduleError(f"You already have {MAX_ITEMS} {kind}. Delete some first.")


def add_routine(name, task, schedule, now=None):
    now = now or datetime.now()
    task = str(task or "").strip()[:MAX_TASK]
    if not task:
        raise ScheduleError("Please say what the routine should do.")
    with _lock:
        data = load()
        check_room(data, "routines")
        item = {"id": new_id(), "name": clean_name(name, task), "task": task, "schedule": schedule,
                "paused": False, "next_run": stamp(next_time(schedule, now)), "last_run": None, "created": stamp(now)}
        data["routines"].append(item)
        save(data)
    return item


def add_reminder(text, moment, now=None):
    text = " ".join(str(text or "").split())[:200]
    if not text:
        raise ScheduleError("Please say what to remind you about.")
    with _lock:
        data = load()
        check_room(data, "reminders")
        item = {"id": new_id(), "text": text, "at": stamp(moment), "created": stamp(now or datetime.now())}
        data["reminders"].append(item)
        save(data)
    return item


def add_workflow(name, task):
    task = str(task or "").strip()[:MAX_TASK]
    if not task:
        raise ScheduleError("Please say what the workflow should do.")
    name = clean_name(name, task)
    with _lock:
        data = load()
        same = [w for w in data["workflows"] if w["name"].lower() == name.lower()]
        if same:  # saving a workflow under the same name again replaces it
            same[0]["task"] = task
            item = same[0]
        else:
            check_room(data, "workflows")
            item = {"id": new_id(), "name": name, "task": task}
            data["workflows"].append(item)
        save(data)
    return item


def label_of(kind, item):
    return item["text"] if kind == "reminders" else item["name"]


def find(name, strict=False):
    """(kind, item) whose name best matches the words (e.g. 'news routine' -> 'Morning news'), or (None, None).
    strict: every word must match (used before deleting, so a half match can't remove the wrong item)."""
    words = [w for w in re.findall(r"[a-z0-9]+", str(name or "").lower())
             if w not in ("the", "my", "routine", "reminder", "workflow", "a", "to", "about")]
    if not words:
        return None, None
    best, best_score = (None, None), 0
    data = load()
    for kind in ("routines", "reminders", "workflows"):
        for item in data[kind]:
            label = (label_of(kind, item) + " " + item.get("task", "")).lower()
            score = sum(1 for w in words if w in label) / len(words)
            if label_of(kind, item).lower() == " ".join(words):
                score = 2  # the exact name wins
            if score > best_score:
                best, best_score = (kind, item), score
    return best if best_score >= (1 if strict else 0.5) else (None, None)


def change(item_id, **fields):
    """Change fields of one item by id. Returns the item (or None)."""
    with _lock:
        data = load()
        for kind in data:
            for item in data[kind]:
                if item["id"] == item_id:
                    item.update(fields)
                    if kind == "routines" and fields.get("paused") is False:  # resumed: from now on
                        item["next_run"] = stamp(next_time(item["schedule"], datetime.now()))
                    save(data)
                    return item
    return None


def remove(item_id):
    with _lock:
        data = load()
        for kind in data:
            kept = [item for item in data[kind] if item["id"] != item_id]
            if len(kept) != len(data[kind]):
                data[kind] = kept
                save(data)
                return True
    return False


def find_workflow(text):
    """'study mode' / 'run study mode' / 'start study mode workflow' -> that workflow, else None."""
    words = re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower())
    words = re.sub(r"^\s*(?:please\s+)?(?:run|start|do|begin|launch)\s+(?:my\s+|the\s+)?", "", words)
    words = " ".join(re.sub(r"\s+(?:workflow|mode please|please)\s*$", "", words).split())
    for item in load()["workflows"]:
        name = " ".join(re.sub(r"[^a-z0-9 ]", " ", item["name"].lower()).split())
        if words and (words == name or words == name + " workflow"):
            return item
    return None


def summary(now=None):
    """Everything, with readable times, for the Automations panel and the Automate agent's list."""
    now = now or datetime.now()
    data = load()
    routines = []
    for r in data["routines"]:
        upcoming = parse_stamp(r.get("next_run"))
        routines.append({**r, "when": describe(r["schedule"]),
                         "next": "paused" if r.get("paused") else (when_text(upcoming, now) if upcoming else "finished")})
    reminders = []
    for r in sorted(data["reminders"], key=lambda r: r["at"]):
        reminders.append({**r, "when": when_text(parse_stamp(r["at"]), now)})
    return {"routines": routines, "reminders": reminders, "workflows": data["workflows"]}


# ---------------------------------------------------------------
# The clock
# ---------------------------------------------------------------
class Scheduler:
    """
    A background thread that wakes up every TICK seconds and runs what is due:
      on_reminder(reminder, late)  -> show it (late = it was missed while Aura was closed)
      on_routine(routine) -> start it; returns False if Aura is busy (it is tried again next tick)
    """

    def __init__(self, on_reminder, on_routine):
        self.on_reminder = on_reminder
        self.on_routine = on_routine
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        if not self._thread.is_alive():  # the page may load more than once
            self._thread.start()

    def _loop(self):
        while True:
            try:
                self.tick(datetime.now())
            except Exception as e:  # a bad entry must never stop the clock
                print(f"  [scheduler] {e}")
            time.sleep(TICK)

    def tick(self, now):
        due_reminders, due_routines = [], []
        with _lock:
            data = load()
            for r in data["reminders"]:
                at = parse_stamp(r["at"])
                if at is None or at <= now:
                    due_reminders.append(r)
            data["reminders"] = [r for r in data["reminders"] if r not in due_reminders]

            for r in data["routines"]:
                upcoming = parse_stamp(r.get("next_run"))
                if r.get("paused") or upcoming is None or upcoming > now:
                    continue
                if now - upcoming > timedelta(minutes=LATE_LIMIT):  # missed while Aura was closed
                    r["next_run"] = stamp(next_time(r["schedule"], now))
                    r["last_status"] = "missed"
                    continue
                due_routines.append(r)
            save(data)

        for r in due_reminders:
            at = parse_stamp(r["at"])
            self.on_reminder(r, late=bool(at and now - at > timedelta(minutes=2)))

        for r in due_routines:  # one at a time: Aura runs one request at a time
            if not self.on_routine(r):
                return  # busy: try again at the next tick
            with _lock:
                data = load()
                for item in data["routines"]:
                    if item["id"] == r["id"]:
                        item["last_run"] = stamp(now)
                        item["next_run"] = stamp(next_time(item["schedule"], now))
                        item["last_status"] = "ran"
                # a one-time routine that has run is finished: remove it
                data["routines"] = [i for i in data["routines"]
                                    if not (i["id"] == r["id"] and i["next_run"] is None)]
                save(data)
            return  # the next due routine waits until this one has finished
