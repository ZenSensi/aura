"""
logger.py - Saves one line of JSON for every agent step.

File: logs/run_log.jsonl  ("JSON Lines" = one JSON object per line,
easy to append to and easy to load later, e.g. with pandas.read_json(..., lines=True)).

Each line has: timestamp, request_id, agent, model, input, output,
seconds, success, fallback_used, error.
"""

import json
import os
from datetime import datetime

import config

LOG_FILE = os.path.join(config.LOG_DIR, "run_log.jsonl")
MAX_TEXT = 500  # cut long outputs so the log stays readable
MAX_LOG_BYTES = 5_000_000  # past this, the log moves to run_log.1.jsonl (replacing the older one) and starts afresh
# These agents handle letters, emails, messages and private files/pages: their text is not kept, only its length
PRIVATE_AGENTS = {"writer", "mailer", "whatsapp", "inbox", "reader", "notion"}


def short(text):
    """Shorten long text for the log."""
    text = str(text)
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "..."


def log_step(request_id, agent, model, input_text, output, seconds, success, fallback_used=False, error=None):
    """Print one step and append it to logs/run_log.jsonl."""
    entry = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "request_id": request_id,
        "agent": agent,
        "model": model,
        "input": f"[{len(str(input_text))} characters]" if agent in PRIVATE_AGENTS else short(input_text),
        "output": f"[{len(str(output))} characters]" if agent in PRIVATE_AGENTS else short(output),
        "seconds": round(seconds, 2),
        "success": success,
        "fallback_used": fallback_used,
        "error": error,
    }

    status = "OK  " if success else "FAIL"
    print(f"  [log] {status} {agent:12} {entry['seconds']:>6}s  {model}")

    if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > MAX_LOG_BYTES:
        os.replace(LOG_FILE, os.path.join(config.LOG_DIR, "run_log.1.jsonl"))
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return entry


def read_log():
    """Load all log entries as a list of dicts (empty list if no log yet)."""
    if not os.path.exists(LOG_FILE):
        return []
    entries = []
    with open(LOG_FILE, encoding="utf-8") as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except ValueError:  # blank, or cut off by a crash: skip it, keep the rest
                continue
    return entries


def usage():
    """
    For the sidebar's Agents list: how many requests each agent did its job in,
    and when it last did: {"music": {"count": 12, "last": "2026-10-19T01:41:48"}, ...}.
    The Orchestrator counts every request it planned.
    """
    found = {}
    for entry in read_log():
        if not entry["success"]:
            continue
        agent = found.setdefault(entry["agent"], {"requests": set(), "last": ""})
        agent["requests"].add(entry["request_id"])
        agent["last"] = max(agent["last"], entry["timestamp"])
    return {name: {"count": len(a["requests"]), "last": a["last"]} for name, a in found.items()}


def recent(limit=8):
    """
    Your latest requests for the sidebar's "Recent tasks", newest first:
    [{"text", "time", "status": "ok" | "failed" | "nothing", "agents": [...]}].
    The same request typed twice is shown once. Requests about an attached
    file are left out: running them again would need the file again.
    """
    requests = {}  # request_id -> its log lines, in order
    for entry in read_log():
        requests.setdefault(entry["request_id"], []).append(entry)

    found, seen = [], set()
    for lines in reversed(list(requests.values())):
        plan = next((e for e in lines if e["agent"] == "orchestrator"), None)
        if not plan:
            continue
        agents = [e["agent"] for e in lines if e["agent"] != "orchestrator"]
        text = plan["input"]
        if "reader" in agents or text.lower() in seen:
            continue
        seen.add(text.lower())
        failed = not all(e["success"] for e in lines)
        found.append({
            "text": text,
            "time": plan["timestamp"],
            "status": "failed" if failed else ("ok" if agents else "nothing"),
            "agents": list(dict.fromkeys(agents)),  # each agent once, in order
        })
        if len(found) == limit:
            break
    return found

