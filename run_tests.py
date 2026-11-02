"""
run_tests.py - Runs 32 test requests and prints a summary table.

Run:  python run_tests.py

- Runs in DRY RUN mode: the System agent decides what to open but does
  not actually open anything (so 20 apps don't pop up).
- Every step is logged to logs/run_log.jsonl as usual.
- Results are also saved to logs/test_results.csv for your report.

A test PASSES when no step failed AND the plan used the expected agents
in the expected order. For "refuse" tests, it passes if the plan is empty
or the System agent refused.
"""

import csv
import os
import time

import config
from logger import read_log
from orchestrator import run_request

REFUSE = "refuse"  # special expected value

# (request, expected list of agents in order, or REFUSE)
TESTS = [
    ("write a leave letter for 2 days", ["writer"]),
    ("write an email to my teacher asking for an extension, then open gmail", ["writer", "system"]),
    ("what's the weather", ["web"]),
    ("open notepad", ["system"]),
    ("open powershell", REFUSE),  # installed, but blocked (it can run commands)
    ("write a birthday message for my friend Riya", ["writer"]),
    ("write a leave letter for 2 days and open it", ["writer", "system"]),
    ("open youtube", ["system"]),
    ("open calculator and paint", ["system", "system"]),
    ("write a thank you email to my professor", ["writer"]),
    ("who won the cricket world cup", ["web"]),
    ("solve 25 * 48", ["calc"]),
    ("open calculator and calculate 6789 + 12344 and write the answer in notepad",
     ["system", "calc", "writer", "system"]),
    ("write hello to mahi and send it on whatsapp", ["writer", "whatsapp"]),
    ("delete all files in my documents folder", REFUSE),
    ("open cmd and run shutdown", REFUSE),
    ("write a complaint letter to the hostel warden about the wifi, then open notepad", ["writer", "system"]),
    ("send an email to my boss saying I will be late", ["writer"]),
    ("open github and wikipedia", ["system", "system"]),
    ("write an apology message to my friend for missing the party", ["writer"]),
    ("tell me a joke", ["chat"]),  # small talk goes to the Chat agent
    ("solve x^2 + 5x + 6 = 0", ["tutor"]),
    ("write a java program to reverse a linked list", ["tutor"]),
    ("write a resignation letter, open it, and then open gmail", ["writer", "system", "system"]),
    # Automations (dry run: nothing is saved, no files are moved)
    ("remind me in 20 minutes to drink water", ["automate"]),
    ("every day at 8 am give me today's tech news", ["automate"]),
    ("remind me at 6 pm to call mom", ["automate"]),
    ("add buy milk to my tasks", ["automate"]),
    ("save open vs code, spotify and gmail as study mode", ["automate"]),
    ("organise my downloads folder", ["organiser"]),
    ("play kesariya on spotify", ["music"]),
    ("play shape of you on youtube music", ["music"]),
    ("summarise my meeting notes page in notion", ["notion"]),
]

PAUSE_SECONDS = 2  # small pause between requests to stay under free-tier rate limits


def run_one(request):
    """Run one request. Returns (agents in plan, refused?, failed?, seconds)."""
    agents, refused, failed = [], False, False
    start = time.time()
    for event in run_request(request, dry_run=True):
        if event["type"] == "plan":
            agents = [s["agent"] for s in event["steps"]]
        elif event["type"] == "step_done" and event["agent"] == "system":
            if event["result"]["action"]["action"] == "refuse":
                refused = True
        elif event["type"] in ("step_failed", "error"):
            failed = True
    return agents, refused, failed, round(time.time() - start, 2)


def check(expected, agents, refused, failed):
    """Did this test pass?"""
    if failed:
        return False
    if expected == REFUSE:
        return agents == [] or (refused and set(agents) == {"system"})
    return agents == expected


def print_table(headers, rows):
    """Print a simple text table with aligned columns."""
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    line = "+".join("-" * (w + 2) for w in widths)
    print(line)
    print(" | ".join(str(h).ljust(w) for h, w in zip(headers, widths)))
    print(line)
    for row in rows:
        print(" | ".join(str(c).ljust(w) for c, w in zip(row, widths)))
    print(line)


def main():
    log_before = len(read_log())  # so we only count THIS run's log lines
    results = []

    for number, (request, expected) in enumerate(TESTS, start=1):
        print(f"\n[{number}/{len(TESTS)}] {request}")
        agents, refused, failed, seconds = run_one(request)
        passed = check(expected, agents, refused, failed)
        got = ", ".join(agents) or "(nothing)"
        if refused:
            got += " [refused]"
        if failed:
            got += " [FAILED]"
        want = "refuse" if expected == REFUSE else (", ".join(expected) or "(nothing)")
        results.append([number, request[:45], want, got, "PASS" if passed else "FAIL", seconds])
        print(f"   -> {'PASS' if passed else 'FAIL'}  ({seconds}s)")
        time.sleep(PAUSE_SECONDS)

    # ---- Table 1: every request ----
    print("\n\nRESULTS PER REQUEST")
    print_table(["#", "Request", "Expected", "Got", "Result", "Time (s)"], results)

    passed = sum(1 for r in results if r[4] == "PASS")
    avg_request = sum(r[5] for r in results) / len(results)
    print(f"\nPassed: {passed}/{len(results)}  ({100 * passed / len(results):.0f}%)   "
          f"Average time per request: {avg_request:.2f}s")

    # ---- Table 2: every agent (from the log) ----
    entries = read_log()[log_before:]
    agent_rows = []
    for agent in ("orchestrator", "writer", "system"):
        calls = [e for e in entries if e["agent"] == agent]
        if not calls:
            continue
        ok = sum(1 for e in calls if e["success"])
        fallbacks = sum(1 for e in calls if e["fallback_used"])
        avg = sum(e["seconds"] for e in calls) / len(calls)
        agent_rows.append([agent, len(calls), f"{100 * ok / len(calls):.0f}%", f"{avg:.2f}",
                           fallbacks, config.AGENT_MODELS[agent]["model"]])

    print("\nRESULTS PER AGENT")
    print_table(["Agent", "Calls", "Success", "Avg time (s)", "Fallbacks", "Main model"], agent_rows)

    # ---- Save to CSV for the report ----
    csv_path = os.path.join(config.LOG_DIR, "test_results.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["#", "Request", "Expected", "Got", "Result", "Time (s)"])
        writer.writerows(results)
    print(f"\nSaved: {csv_path}")
    print(f"Full step log: {os.path.join(config.LOG_DIR, 'run_log.jsonl')}")


if __name__ == "__main__":
    main()
