"""
agents/automate.py - The Automate agent.

Saves things for Aura to do LATER or AGAIN (see automations.py):
  "remind me at 5 PM to call papa"                  -> a reminder on this PC AND on the phone
                                                       (Google Tasks + Calendar, see google_sync.py)
  "add buy milk to my tasks"                        -> a to-do in Google Tasks (no time)
  "every day at 8 AM give me the news"              -> a routine (runs "give me the news")
  "at 9 PM send "good night" to mummy"              -> a one-time routine
  "save 'open VS Code, Spotify and Gmail' as study mode" -> a workflow ("study mode" runs it)
  "show my routines", "pause the news routine", "delete the water reminder"

SAFETY DESIGN (like the System agent):
  The model only fills in a small JSON form: which action, a name, the
  request to run, and WHEN in words it read ("17:00", "tomorrow", "every
  monday"). Python checks every field and works out the real times itself
  (automations.build_schedule). Nothing runs now: a routine runs later
  through the normal Orchestrator, with all of its safety checks.
"""

import time
from datetime import datetime

import automations
import google_sync
from automations import ScheduleError
from google_sync import GoogleError
from llm import call_llm_json

MORNING = 9  # hour the PC reminds you of a task that has only a day ("tomorrow")
ACTIONS = ("reminder", "routine", "workflow", "list", "remove", "pause", "resume", "refuse")

PROMPT = """You are the Automate agent of a desktop assistant called Aura. You SAVE things to do later or again.
Now it is {now}.
Saved now: {saved}

Turn the user's message into ONE action:
- "reminder": just remind the user of something (a notification). "text" = what to remember, short,
  e.g. "Call papa". Also "when".
  Adding a task / to-do ("add buy milk to my tasks", "put pay fees on my to-do list for tomorrow") is a
  reminder too. It may have no "when" ({{}}), or only a day: {{"repeat": "once", "time": "", "day": "tomorrow"}}.
- "routine": Aura does a task at a time, once or repeating. "task" = the request Aura should run each time,
  in the user's words but WITHOUT the time part, e.g. "give me today's tech news", "summarise my unread emails",
  'send "good night" to mummy' (keep words to send exactly, in double quotes), "organise my downloads folder".
  "name" = a short title (2-4 words), e.g. "Morning news". Also "when".
- "workflow": save a request under a name to run it later by name (no time). "name" = its name
  (e.g. "study mode"), "task" = the request, e.g. "open VS Code, Spotify and Gmail".
- "list": show what is saved.
- "remove" / "pause" / "resume": "name" = words naming the saved item, e.g. "news" or "water".
- "refuse": anything else (reason in "reason").

"when" (only for reminder and routine):
- in N minutes/hours from now: {{"in_minutes": N}}  (2 hours = 120)
- at a time: {{"repeat": "once", "time": "HH:MM" (24-hour), "day": "" | "today" | "tomorrow" | "mon".."sun" | "YYYY-MM-DD"}}
  (day "" when the user didn't say one)
- repeating (routines only): {{"repeat": "daily" | "weekdays" | "weekends", "time": "HH:MM"}},
  {{"repeat": "weekly", "days": ["mon", "thu"], "time": "HH:MM"}}, {{"repeat": "hourly", "minute": 0}},
  {{"repeat": "every", "minutes": N}}
- "morning" = 08:00, "afternoon" = 14:00, "evening" = 18:00, "night" = 21:00 when no time is given.

Reply with ONLY this JSON, nothing else:
{{"action": "...", "name": "", "task": "", "text": "", "when": {{}}, "reason": ""}}"""


def saved_names():
    data = automations.load()
    names = [f"routine '{r['name']}'" for r in data["routines"]]
    names += [f"reminder '{r['text']}'" for r in data["reminders"]]
    names += [f"workflow '{w['name']}'" for w in data["workflows"]]
    return "; ".join(names) or "nothing"


def do_action(data, dry_run, now):
    """Check the model's form and carry it out. Returns (action, name, message)."""
    action = str(data.get("action", "")).strip().lower()
    if action not in ACTIONS:
        return "refuse", "", "I couldn't tell what to save."
    name = str(data.get("name") or "").strip()
    when = data.get("when") if isinstance(data.get("when"), dict) else {}

    if action == "refuse":
        return "refuse", "", str(data.get("reason") or "I can only save reminders, routines and workflows.")

    if action == "reminder":
        return reminder(str(data.get("text") or name).strip(), when, dry_run, now)

    if action == "routine":
        if not when.get("repeat") and when.get("in_minutes") in (None, "", 0) and not when.get("time"):
            raise ScheduleError("Please say when it should run, e.g. \"every day at 8 AM\".")
        schedule = automations.build_schedule(when, now)
        task = str(data.get("task") or "").strip()
        if dry_run:
            return action, name, f"[dry run] Would save routine '{name}': {task} ({automations.describe(schedule)})"
        item = automations.add_routine(name, task, schedule, now)
        return action, item["name"], (f"Routine saved: {item['name']} · {automations.describe(schedule)}. "
                                      f"It will run \"{item['task']}\".")

    if action == "workflow":
        task = str(data.get("task") or "").strip()
        if dry_run:
            return action, name, f"[dry run] Would save workflow '{name}': {task}"
        item = automations.add_workflow(name, task)
        return action, item["name"], f"Workflow saved: say \"{item['name']}\" to run \"{item['task']}\"."

    if action == "list":
        return action, "", ""  # run_automate adds the list

    # remove / pause / resume one saved item
    kind, item = automations.find(name or data.get("text") or data.get("task"))
    if not item:
        return "refuse", name, f"I couldn't find a saved routine, reminder or workflow called '{name}'."
    label = automations.label_of(kind, item)
    if action == "pause" and kind != "routines" or action == "resume" and kind != "routines":
        return "refuse", label, "Only routines can be paused and resumed."
    if dry_run:
        return action, label, f"[dry run] Would {action} '{label}'"
    if action == "remove":
        automations.remove(item["id"])
        return action, label, f"Deleted the {kind[:-1]} '{label}'."
    automations.change(item["id"], paused=action == "pause")
    return action, label, f"{'Paused' if action == 'pause' else 'Resumed'} the routine '{label}'."


def reminder(text, when, dry_run, now):
    """
    A reminder goes to BOTH places:
      - this PC: a notification at the time (Scheduler in automations.py)
      - the phone: a Google Tasks task, plus a Calendar event that rings at the time
    Only a day ("tomorrow"): the task is dated, and the PC reminds at MORNING on that day.
    No time at all ("add buy milk to my tasks"): only the Google Tasks to-do.
    """
    has_time = bool(when.get("time")) or when.get("in_minutes") not in (None, "", 0)
    pc_moment = None
    if has_time:
        moment = pc_moment = automations.one_time(when, now)
        said = automations.when_text(moment, now)
    elif when.get("day"):
        day = automations.resolve_day(when["day"], now) or now.date()
        moment = datetime.combine(day, datetime.min.time())
        said = "today" if day == now.date() else "tomorrow" if (day - now.date()).days == 1 else day.strftime("%a %d %b")
        pc_moment = moment.replace(hour=MORNING)
        if pc_moment <= now:
            pc_moment = None  # "today", said after 9 AM: the phone's task is enough
    else:
        moment, said = None, ""
    text = " ".join(text.split())

    if dry_run:
        places = (["this PC"] if pc_moment else []) + (["phone"] if google_sync.ready() else [])
        return "reminder", text, f"[dry run] Would remind you ({' + '.join(places)}){' ' + said if said else ''}: {text}"

    if pc_moment:
        text = automations.add_reminder(text, pc_moment, now)["text"]
    on_phone, problem = False, ""
    if google_sync.ready():  # Google set up (google_client.json next to Aura)
        try:
            google_sync.add_to_phone(text, moment, has_time)
            on_phone = True
        except GoogleError as e:
            problem = f" (Couldn't add it to your phone: {e})"
    if not pc_moment and not on_phone:
        return "refuse", text, problem.strip(" ()") or "Please say when to remind you, e.g. \"at 5 PM\"."

    if has_time:
        message = f"Reminder set on this PC{' and your phone' if on_phone else ''}, {said}: {text}"
    elif pc_moment:
        start = "Added to your phone's tasks; this PC" if on_phone else "This PC"
        message = f"{start} will remind you {said} at {automations.clock(pc_moment)}: {text}"
    else:
        message = f"Added to your phone's tasks{', for ' + said if said else ''}: {text}"
    message += problem
    return "reminder", text, message


def run_automate(task, state):
    """Run the Automate agent on the user's own words (state["request"])."""
    start = time.time()
    now = datetime.now()
    prompt = PROMPT.format(now=now.strftime("%A %d %B %Y, %H:%M"), saved=saved_names())
    reply = call_llm_json("automate", prompt, state.get("request") or task)
    when = reply["data"].get("when")
    words = (state.get("request") or task).lower()
    if isinstance(when, dict) and when.get("day") == "today" and "today" not in words and "tonight" not in words:
        when["day"] = ""  # some backup models write "today" unasked: "at 6" said at 11 PM = tomorrow, not an error
    try:
        action, name, message = do_action(reply["data"], state.get("dry_run"), now)
    except ScheduleError as e:
        action, name, message = "refuse", "", str(e)

    result = {
        "agent": "automate", "model": reply["model"], "seconds": round(time.time() - start, 2),
        "fallback_used": reply["fallback_used"],
        "action": {"action": action, "target": name, "reason": message if action == "refuse" else ""},
        "message": message,
    }
    if action == "list":
        saved = automations.summary(now)
        result["saved"] = saved
        counts = [f"{len(saved[k])} {k if len(saved[k]) != 1 else k[:-1]}" for k in ("routines", "reminders", "workflows")]
        result["message"] = "You have " + ", ".join(counts) + "."
    return result
