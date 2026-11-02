"""
agents/mailer.py - The Mailer agent.

Sends the last email the Writer wrote, from your Gmail account,
using Gmail's SMTP server and a Gmail "App Password" from .env.

SAFETY DESIGN (sending cannot be undone, so it is strict):
  - No AI model here. Plain Python decides everything, so a model
    can never invent a recipient or change the email.
  - It only sends to email addresses that YOU typed in your request.
    An address that only appears in the model's task text is ignored.
  - Replies go to the REAL sender of the email the Inbox agent found
    (read from Gmail by Python), and stay in the same Gmail thread.
  - It only sends the email the Writer saved in THIS request.
  - It refuses if the draft still has blanks like [Your Name].
  - At most MAX_RECIPIENTS addresses. Nothing is sent in test (dry run) mode.
"""

import os
import re
import smtplib
import time
from email.message import EmailMessage

import config

EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PLACEHOLDER_PATTERN = re.compile(r"\[[^\]\n]{2,40}\]")  # e.g. [Your Name]


def find_emails(text):
    """All email addresses in a piece of text, lower-case, without repeats."""
    found = []
    for address in EMAIL_PATTERN.findall(text or ""):
        address = address.lower().rstrip(".")
        if address not in found:
            found.append(address)
    return found


def split_subject(content):
    """'Subject: Hello\\n\\nDear...' -> ('Hello', 'Dear...')."""
    first, _, rest = content.partition("\n")
    if first.lower().startswith("subject:"):
        return first[len("subject:"):].strip() or "No subject", rest.strip()
    return "No subject", content.strip()


def make_result(action, target, message, start):
    """Result in the same shape as the System agent's, so the UI and logs can show it."""
    return {
        "agent": "mailer", "model": "none (plain Python)", "seconds": round(time.time() - start, 2),
        "fallback_used": False,
        "action": {"action": action, "target": target},
        "message": message,
    }


def refuse(reason, start):
    return make_result("refuse", "", "Not sent: " + reason, start)


def run_mailer(task, state):
    """
    Run the Mailer agent on one task, e.g. "send the last email to someone@gmail.com".
    1) pick recipients  2) run the safety checks  3) send with Gmail SMTP
    """
    start = time.time()
    sender = config.GMAIL_ADDRESS

    # 1. Recipients: in the task AND typed by the user, never our own address
    typed_by_user = find_emails(state.get("request", ""))
    recipients = [a for a in find_emails(task) if a in typed_by_user and a != sender]
    if not recipients:  # the task lost the address? use what the user typed
        recipients = [a for a in typed_by_user if a != sender]
    original = state.get("reply_to") if "repl" in task.lower() else None
    if not recipients and original:  # replying: send to whoever sent that email
        recipients = [original["from_address"]]

    # 2. Safety checks
    if not recipients:
        return refuse("no email address was given in your request.", start)
    if len(recipients) > config.MAX_RECIPIENTS:
        return refuse(f"more than {config.MAX_RECIPIENTS} recipients.", start)
    path = state.get("last_file")
    if not path or state.get("last_type") != "email" or not os.path.isfile(path):
        return refuse("there is no email written in this request to send.", start)
    with open(path, encoding="utf-8") as f:
        subject, body = split_subject(f.read())
    blanks = PLACEHOLDER_PATTERN.findall(subject + body)
    if blanks:
        return refuse(f"the draft still has blanks to fill in ({', '.join(blanks[:3])}). "
                      "Add the missing details to your request and try again.", start)

    to_text = ", ".join(recipients)
    if state.get("dry_run"):  # test mode: never send
        return make_result("send_email", to_text, f"[dry run] Would send '{subject}' to {to_text}", start)

    if not sender or not config.GMAIL_APP_PASSWORD:
        raise ValueError("Email sending is not set up. Add GMAIL_ADDRESS and GMAIL_APP_PASSWORD "
                         "to your .env file (see README).")

    # 3. Send through Gmail (SSL connection, logged in with the App Password)
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to_text
    message["Subject"] = subject
    if original and recipients == [original["from_address"]]:
        # Same thread in Gmail: "Re: <original subject>" + reply headers
        if not original["subject"].lower().startswith("re:"):
            message.replace_header("Subject", "Re: " + original["subject"])
        else:
            message.replace_header("Subject", original["subject"])
        if original.get("message_id"):
            message["In-Reply-To"] = original["message_id"]
            message["References"] = original["message_id"]
    message.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=config.API_TIMEOUT_SECONDS) as server:
        server.login(sender, config.GMAIL_APP_PASSWORD)
        server.send_message(message)

    return make_result("send_email", to_text, f"Sent '{message['Subject']}' from {sender} to {to_text}", start)
