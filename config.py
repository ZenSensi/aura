"""
config.py - All settings for Aura live here.

Change models, providers, limits and allowlists in THIS file only.
No other file should hard-code a model name or URL.

NOTE: Model names change often! Before running, check the current
model names on each provider's website:
  - Gemini:     https://ai.google.dev/gemini-api/docs/models
  - Groq:       https://console.groq.com/docs/models
  - OpenRouter: https://openrouter.ai/models?max_price=0   (free models)
"""

import importlib
import os
import sys
from dotenv import load_dotenv, set_key

# Folder where .env, outputs/ and logs/ live.
# - Normal run (python desktop.py): the folder of this file.
# - Packaged .exe (PyInstaller):    %LOCALAPPDATA%\Aura, so every Windows user
#   gets their own keys and files and the .exe can live anywhere (even Program Files).
#   A .env next to the .exe keeps the old "portable" mode: files stay beside it.
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
    if not os.path.isfile(os.path.join(BASE_DIR, ".env")):
        BASE_DIR = os.path.join(os.getenv("LOCALAPPDATA") or os.path.expanduser("~"), "Aura")
        os.makedirs(BASE_DIR, exist_ok=True)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Read API keys from the .env file into environment variables
ENV_FILE = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_FILE)


def save_env(values):
    """Settings panel: write {"NAME": "value"} to .env and use it at once.
    Every file reads config.X when it runs, so reloading this file is enough."""
    open(ENV_FILE, "a").close()  # set_key needs the file to exist
    for name, value in values.items():
        set_key(ENV_FILE, name, value)
        os.environ[name] = value
    importlib.reload(sys.modules[__name__])

# ---------------------------------------------------------------
# 1. Providers
# Every provider here speaks the "OpenAI-compatible" chat API,
# so one function in llm.py can talk to all of them.
# ---------------------------------------------------------------
PROVIDERS = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "api_key": os.getenv("GEMINI_API_KEY"),
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "api_key": os.getenv("GROQ_API_KEY"),
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": os.getenv("OPENROUTER_API_KEY"),
    },
}

# ---------------------------------------------------------------
# 2. Which model each agent uses (per-agent routing)
# ---------------------------------------------------------------
# Each agent has a main model and a "fallback" model on a DIFFERENT
# provider. If the main one errors or is rate-limited, llm.py tries
# the fallback automatically.
# An entry may set its own "api_key"; otherwise the provider's key is used.
# (The System agent uses a second Groq key so it has its own rate limit.)
#
# If the fallback ALSO fails, every agent tries LAST_RESORT (see below).
AGENT_MODELS = {
    "orchestrator": {
        "provider": "groq", "model": "openai/gpt-oss-120b",  # Gemini's free quota 429'd on most requests
        "api_key": os.getenv("GROQ_API_KEY_SYSTEM"),        # second key, so it doesn't share the Writer's limit
        "fallback": {"provider": "gemini", "model": "gemini-3.5-flash"},
    },
    "writer": {
        "provider": "groq", "model": "openai/gpt-oss-120b",
        "fallback": {"provider": "gemini", "model": "gemini-3.5-flash"},
    },
    "system": {
        "provider": "groq", "model": "qwen/qwen3.8-27b",     # fast (llama-3.1-8b-instant was retired by Groq)
        "api_key": os.getenv("GROQ_API_KEY_SYSTEM"),
        "fallback": {"provider": "gemini", "model": "gemini-3.5-flash-lite"},
    },
    "news": {
        "provider": "groq", "model": "openai/gpt-oss-120b",  # plans the briefing + summarises headlines
        "fallback": {"provider": "gemini", "model": "gemini-3.5-flash"},
    },
    "files": {
        "provider": "groq", "model": "openai/gpt-oss-20b",   # turns a request into a file search plan
        "api_key": os.getenv("GROQ_API_KEY_SYSTEM"),
        "fallback": {"provider": "gemini", "model": "gemini-3.5-flash-lite"},
    },
    "inbox": {
        "provider": "groq", "model": "openai/gpt-oss-120b",  # reads + summarises emails
        "api_key": os.getenv("GROQ_API_KEY_SYSTEM"),        # its own rate limit, not the Writer's
        "fallback": {"provider": "gemini", "model": "gemini-3.5-flash"},
    },
    "web": {
        "provider": "groq", "model": "openai/gpt-oss-120b",  # answers from the search results
        "fallback": {"provider": "gemini", "model": "gemini-3.5-flash"},
    },
    "chat": {
        "provider": "groq", "model": "openai/gpt-oss-120b",  # conversation with the user
        "fallback": {"provider": "gemini", "model": "gemini-3.5-flash"},
    },
    "tutor": {
        "provider": "groq", "model": "openai/gpt-oss-120b",  # maths, science and code, step by step
        "options": {"reasoning_effort": "high"},             # thinks longer before answering (~3 s)
        "fallback": {"provider": "gemini", "model": "gemini-3.5-flash"},
    },
    "automate": {
        "provider": "groq", "model": "openai/gpt-oss-120b",  # reads "every day at 8 AM..." into a small form
        "fallback": {"provider": "gemini", "model": "gemini-3.5-flash"},
    },
    "notion": {
        "provider": "groq", "model": "openai/gpt-oss-120b",  # plans Notion actions + answers about a page
        "api_key": os.getenv("GROQ_API_KEY_SYSTEM"),
        "fallback": {"provider": "gemini", "model": "gemini-3.5-flash"},
    },
    "reader": {
        "provider": "groq", "model": "openai/gpt-oss-120b",  # reads PDFs, Word and text files
        "fallback": {"provider": "gemini", "model": "gemini-3.5-flash"},
    },
    "vision": {  # the Reader's model for pictures and scanned PDFs (it can see images)
        "provider": "groq", "model": "qwen/qwen3.8-27b",
        # No backup: free vision endpoints keep logs, and these are the user's own photos and scans
        "no_last_resort": True,
    },
}

# Web search (Web agent): Tavily's search API, made for AI agents.
# Free key (1,000 searches a month, no card) at https://tavily.com -> TAVILY_API_KEY in .env
TAVILY_API_KEY = (os.getenv("TAVILY_API_KEY") or "").strip()
WEB_RESULTS = 5           # search results the Web agent reads per search

# Notion agent: an internal integration's secret (notion.so/profile/integrations) -> NOTION_TOKEN in .env.
# Aura only sees the pages you share with that integration.
NOTION_TOKEN = (os.getenv("NOTION_TOKEN") or "").strip()

# Attached files (Reader agent): the + button in the chat box
MAX_ATTACHMENT_MB = 20
MAX_FILE_CHARS = 15000    # text sent to the model from one PDF / document

# Voice typing (voice.py): speech-to-text model (Whisper, free on Groq).
VOICE_MODEL = {"provider": "groq", "model": "whisper-large-v3-turbo"}
VOICE_LANGUAGE = "en"   # the language you speak; without it Whisper sometimes guesses a wrong one ("hi" = Hindi)
MAX_VOICE_SECONDS = 60  # a recording stops by itself after this

# Last backup for every agent, used only if the main AND fallback models fail.
# "openrouter/free" is OpenRouter's free router: it picks one of the free
# models available on OpenRouter for each call. Needs OPENROUTER_API_KEY.
LAST_RESORT = {"provider": "openrouter", "model": "openrouter/free"}

# ---------------------------------------------------------------
# 3. Hard limits
# ---------------------------------------------------------------
MAX_STEPS = 5             # max steps the orchestrator may plan
API_TIMEOUT_SECONDS = 60  # max wait for one API call
JSON_RETRIES = 1          # how many times to re-ask if the reply is not valid JSON

# ---------------------------------------------------------------
# 3b. Email sending (Mailer agent)
# Gmail needs an "App Password" (not your normal password):
# Google Account -> Security -> 2-Step Verification ON -> App passwords.
# ---------------------------------------------------------------
GMAIL_ADDRESS = (os.getenv("GMAIL_ADDRESS") or "").strip().lower()
GMAIL_APP_PASSWORD = (os.getenv("GMAIL_APP_PASSWORD") or "").replace(" ", "")
USER_NAME = (os.getenv("USER_NAME") or "").strip()  # used to sign emails
MAX_RECIPIENTS = 3

# WhatsApp contacts: {"name": "phone number with country code"}, kept only on this PC.
# The WhatsApp agent only sends to names from this file that YOU typed in the request.
CONTACTS_FILE = os.path.join(BASE_DIR, "contacts.json")

# The dock (Settings > Dock): where it sits, the shortcut that opens it, and
# autohide (only shown by the shortcut, hidden again 10 s after you stop using it)
DOCK_POSITIONS = ("top-left", "top-mid", "top-right", "bottom-left", "bottom-mid", "bottom-right")
DOCK_POSITION = os.getenv("DOCK_POSITION") if os.getenv("DOCK_POSITION") in DOCK_POSITIONS else "top-mid"
DOCK_SHORTCUT = os.getenv("DOCK_SHORTCUT") or "`+Space"  # hold ` and press Space (hotkey.py)
DOCK_AUTOHIDE = os.getenv("DOCK_AUTOHIDE") == "1"

# ---------------------------------------------------------------
# 4. Folders
# ---------------------------------------------------------------
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")
LOG_DIR = os.path.join(BASE_DIR, "logs")

# Make sure the folders exist
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

# ---------------------------------------------------------------
# 5. Allowlists for the System agent
# The System agent can ONLY open things listed here. The model picks a
# name (e.g. "notepad"); Python looks up the exact command below.
# The model never writes a command itself.
# Each command is a list (no shell), one per operating system.
# ---------------------------------------------------------------
ALLOWED_APPS = {
    "notepad": {
        "windows": ["notepad.exe"],
        "mac":     ["open", "-a", "TextEdit"],
        "linux":   ["gedit"],
    },
    "calculator": {
        "windows": ["calc.exe"],
        "mac":     ["open", "-a", "Calculator"],
        "linux":   ["gnome-calculator"],
    },
    "paint": {
        "windows": ["mspaint.exe"],
        "mac":     ["open", "-a", "Preview"],
        "linux":   ["kolourpaint"],
    },
    "file explorer": {
        "windows": ["explorer.exe"],
        "mac":     ["open", "."],
        "linux":   ["xdg-open", "."],
    },
}

# Besides the apps above, the System agent can open ANY app installed in
# the Start Menu (Windows) or /Applications (Mac) - see agents/system.py.
# Apps whose name contains one of these words are NEVER opened, because
# they can run commands or change the system:
BLOCKED_APP_WORDS = [
    "command prompt", "cmd", "powershell", "terminal", "command line", "console", "shell",
    "bash", "wsl", "ubuntu", "python", "node.js", "registry", "regedit", "uninstall",
    "task scheduler", "services", "group policy", "system configuration", "computer management",
    "disk management", "recovery", "reset", "windows tools", "control panel", "remote desktop",
]

# Start-menu entries that launch one of these programs are never opened either, whatever their name says.
# Matched against the entry's target (the AppID), so a renamed shortcut can't get past the name check.
BLOCKED_APP_TARGETS = {
    "cmd.exe", "powershell.exe", "powershell_ise.exe", "pwsh.exe", "wt.exe", "wsl.exe", "bash.exe", "conhost.exe",
    "regedit.exe", "mmc.exe", "wscript.exe", "cscript.exe", "mshta.exe", "rundll32.exe", "msiexec.exe", "control.exe",
    "python.exe", "pythonw.exe", "py.exe", "node.exe", "microsoft.windows.shell.rundialog",
}
BLOCKED_APP_PACKAGES = ("microsoft.windowsterminal", "microsoft.powershell", "microsoft.windowssubsystemforlinux",
                        "canonicalgrouplimited", "thedebianproject", "pythonsoftwarefoundation")

ALLOWED_SITES = {
    "gmail":     "https://mail.google.com",
    "google":    "https://www.google.com",
    "youtube":   "https://www.youtube.com",
    "github":    "https://github.com",
    "wikipedia": "https://www.wikipedia.org",
    "chatgpt":   "https://chat.openai.com",
}
