"""
google_sync.py - Puts reminders on the user's phone through Google Tasks and Google Calendar.

"remind me at 6 PM on my phone to call mom"
  -> a task "Call mom" in Google Tasks, dated today (shows in the Tasks app)
  -> a Google Calendar event at 6:00 PM with a pop-up alert (the phone rings at 6)
"add buy milk to my google tasks"
  -> only the task, no date

Why both: Google's Tasks API keeps only the DATE of a task and drops the time,
so a task alone can't ring at 6 PM. The Calendar event does that.

ONE-TIME SETUP (free):
  1. https://console.cloud.google.com -> new project -> enable "Google Tasks API"
     and "Google Calendar API".
  2. "OAuth consent screen": External, add your Gmail as a test user, then
     "Publish app" (in Testing mode Google signs you out every 7 days).
  3. "Credentials" -> Create credentials -> OAuth client ID -> "Desktop app"
     -> download the JSON and save it next to Aura as google_client.json.
  The first phone reminder opens Google's sign-in page in the browser.
  After that, the permission is kept in google_token.json (only on this PC).

SAFETY: Aura asks only for Tasks and Calendar events, nothing else in the
account (no Gmail, no Drive). It only ever ADDS a task or an event.
"""

import os
import threading
from datetime import datetime, timedelta

import config

CLIENT_FILE = os.path.join(config.BASE_DIR, "google_client.json")
TOKEN_FILE = os.path.join(config.BASE_DIR, "google_token.json")
SCOPES = ["https://www.googleapis.com/auth/tasks",
          "https://www.googleapis.com/auth/calendar.events"]
TASKS_URL = "https://tasks.googleapis.com/tasks/v1/lists/@default/tasks"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
EVENT_MINUTES = 15     # how long the Calendar event lasts
SIGN_IN_WAIT = 180     # seconds to wait for the user to finish signing in

_lock = threading.Lock()  # two requests at once must not both open the sign-in page


class GoogleError(Exception):
    """Something went wrong with Google (shown to the user)."""


def ready():
    """True when the one-time setup file is there."""
    return os.path.isfile(CLIENT_FILE)


def _credentials():
    """Saved sign-in, refreshed if old; or a new sign-in in the browser the first time."""
    # Imported here so Aura still starts if these libraries are missing
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not ready():
        raise GoogleError("Google isn't set up yet: save your OAuth client file as google_client.json "
                          f"in {config.BASE_DIR} (see the steps at the top of google_sync.py).")
    with _lock:
        creds = None
        if os.path.isfile(TOKEN_FILE):
            try:
                creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
            except (ValueError, OSError):
                creds = None
        if creds and creds.valid:
            return creds
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                creds = None  # the user removed Aura's access, or it expired: sign in again
        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_FILE, SCOPES)
            try:
                creds = flow.run_local_server(
                    port=0, open_browser=True, timeout_seconds=SIGN_IN_WAIT,
                    success_message="Aura is connected to Google Tasks and Calendar. You can close this tab.")
            except Exception as e:  # closed tab, timeout, "Cancel"...
                raise GoogleError(f"Google sign-in didn't finish ({e}).")
            if not creds:
                raise GoogleError("Google sign-in didn't finish.")
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        return creds


def _post(session, url, body):
    """Send one request to Google. Returns the reply as a dict."""
    try:
        reply = session.post(url, json=body, timeout=config.API_TIMEOUT_SECONDS)
    except Exception as e:
        raise GoogleError(f"Couldn't reach Google ({e}).")
    if reply.status_code >= 400:
        try:
            detail = reply.json().get("error", {}).get("message", "")
        except ValueError:
            detail = reply.text[:200]
        raise GoogleError(f"Google said no ({reply.status_code}): {detail}")
    return reply.json()


def task_body(title, moment=None):
    """A Google Tasks task. Only the date counts: Google drops the time."""
    body = {"title": title[:200], "notes": "Added by Aura"}
    if moment:
        body["due"] = moment.strftime("%Y-%m-%dT00:00:00.000Z")
    return body


def event_body(title, moment):
    """A Calendar event at the moment, with a pop-up alert right when it starts."""
    start = moment.astimezone()  # local time with its offset, e.g. +05:30
    end = start + timedelta(minutes=EVENT_MINUTES)
    return {"summary": title[:200], "description": "Reminder added by Aura",
            "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()},
            "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 0}]}}


def add_to_phone(title, moment=None, has_time=True, session=None):
    """
    Add a task (and, if there is a time, a Calendar event with an alert).
    moment: a datetime, or None for a task with no date.
    has_time: False when the user gave only a day ("tomorrow"), so no Calendar alert.
    Returns a list of what was added, e.g. ["Google Tasks", "Google Calendar"].
    """
    title = " ".join(str(title or "").split())
    if not title:
        raise GoogleError("Please say what to remind you about.")
    if session is None:
        from google.auth.transport.requests import AuthorizedSession
        session = AuthorizedSession(_credentials())
    _post(session, TASKS_URL, task_body(title, moment))
    added = ["Google Tasks"]
    if moment and has_time:
        _post(session, EVENTS_URL, event_body(title, moment))
        added.append("Google Calendar")
    return added
