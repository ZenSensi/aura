"""
main.py - Terminal version of Aura.

The Orchestrator plans each request; we just print the events it yields.
(For the desktop app run:  python desktop.py)

Run:  python main.py
"""

from orchestrator import run_request


def print_event(event):
    """Print one event from run_request() in a readable way."""
    kind = event["type"]

    if kind == "planning":
        print("\n[orchestrator] planning...")

    elif kind == "plan":
        backup = " (fallback)" if event["fallback_used"] else ""
        print(f"[orchestrator] {len(event['steps'])} step(s) in {event['seconds']}s using {event['model']}{backup}")
        for i, step in enumerate(event["steps"], start=1):
            print(f"   {i}. {step['agent']:7} -> {step['task']}")

    elif kind == "step_start":
        print(f"\n[{event['agent']}] step {event['number']}/{event['total']}: {event['task']}")

    elif kind == "step_done":
        result = event["result"]
        backup = " (fallback)" if result["fallback_used"] else ""
        print(f"[{event['agent']}] done in {result['seconds']}s using {result['model']}{backup}")
        if event["agent"] == "writer":
            print(f"[writer] saved to: {result['file']}")
            print("-" * 60)
            print(result["content"])
            print("-" * 60)
        else:
            print(f"[{event['agent']}] {result['message']}")

    elif kind == "step_failed":
        print(f"[{event['agent']}] FAILED: {event['message']}")

    elif kind == "error":
        print(f"[orchestrator] FAILED: {event['message']}")

    elif kind == "done":
        if event.get("nothing_to_do"):
            print("Nothing to do: I can write letters/emails, open apps and sites, read Gmail, "
                  "find files, get the news and do maths.")
        elif event["ok"]:
            print("\nAll steps finished.")
        else:
            print("\nStopped because a step failed.")


def main():
    print("Aura (terminal)")
    print("Type a request, or 'exit' to quit.\n")

    while True:
        try:
            user_text = input("\nYou: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not user_text:
            continue
        if user_text.lower() in ("exit", "quit"):
            print("Bye!")
            break

        for event in run_request(user_text):
            print_event(event)


if __name__ == "__main__":
    main()
