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

import base64
import ctypes
import ctypes.wintypes
import importlib
import json
import os
import sys
from dotenv import dotenv_values, load_dotenv, set_key

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

# Keys and passwords in .env are kept locked by Windows itself (DPAPI): only this Windows account on
# this PC can unlock them, so a copied, synced or backed-up .env gives nothing away. A locked value
# looks like  dpapi:AQAAANCM...  A plain value still works (you can paste a key into .env by hand).
SECRET_NAMES = ("GROQ_API_KEY", "GROQ_API_KEY_SYSTEM", "GEMINI_API_KEY", "OPENROUTER_API_KEY",
                "TAVILY_API_KEY", "GMAIL_APP_PASSWORD", "NOTION_TOKEN")
LOCKED = "dpapi:"


class _Blob(ctypes.Structure):  # DATA_BLOB: a length and a pointer to the bytes
    _fields_ = [("size", ctypes.wintypes.DWORD), ("data", ctypes.c_void_p)]


def _dpapi(data, lock):
    """Windows locks (lock=True) or unlocks these bytes with a key tied to this Windows account."""
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buffer = ctypes.create_string_buffer(data, len(data))
    source, result = _Blob(len(data), ctypes.cast(buffer, ctypes.c_void_p)), _Blob()
    call = crypt32.CryptProtectData if lock else crypt32.CryptUnprotectData
    if not call(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)):
        raise OSError("Windows couldn't lock or unlock a saved key.")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32.LocalFree(result.data)


def lock_secret(value):
    """'gsk_abc' -> 'dpapi:AQAA...' (unchanged when empty, or not on Windows)."""
    if not value or sys.platform != "win32":
        return value
    return LOCKED + base64.b64encode(_dpapi(value.encode("utf-8"), True)).decode("ascii")


def unlock_secret(value):
    """'dpapi:AQAA...' -> 'gsk_abc'. "" if Windows can't unlock it (the .env came from another PC or account)."""
    try:
        return _dpapi(base64.b64decode(value[len(LOCKED):]), False).decode("utf-8")
    except (OSError, ValueError, AttributeError):
        return ""


def _unlock_env():
    """Give the programs the real keys (os.environ) while the .env file keeps only locked ones.
    Aura.exe also locks any plain key it finds. Run from source, .env is left as you typed it."""
    if not os.path.isfile(ENV_FILE):
        return
    for name, value in dotenv_values(ENV_FILE).items():
        if name not in SECRET_NAMES or not value:
            continue
        if value.startswith(LOCKED):
            if os.environ.get(name) == value:  # (after Settings saved one, os.environ already has the real key)
                os.environ[name] = unlock_secret(value)
        elif getattr(sys, "frozen", False):
            try:
                set_key(ENV_FILE, name, lock_secret(value))
            except OSError:
                pass  # couldn't lock it: it stays plain and still works


_unlock_env()


def save_env(values):
    """Settings panel: write {"NAME": "value"} to .env and use it at once.
    Every file reads config.X when it runs, so reloading this file is enough."""
    open(ENV_FILE, "a").close()  # set_key needs the file to exist
    for name, value in values.items():
        set_key(ENV_FILE, name, lock_secret(value) if name in SECRET_NAMES else value)
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

# ---------------------------------------------------------------
# 2b. AI engine: "api" (API keys above) or "cli" (coding-agent CLIs on this PC)
# Settings > AI engine. The chosen engine goes first; if it fails or hits its
# limit, the same call (same messages, so no context is lost) goes to the
# other one: API models -> the agent's CLI, or CLI -> API models with a saved key.
# The prompt goes in on stdin (Windows mangles long arguments to .cmd files).
# Pictures are saved as files next to it for the CLI to look at ("image_flag": attached instead).
# Each runs in an empty folder, read-only, so it can only answer, not act:
# Aura's own agents still do every action.
# ---------------------------------------------------------------
AI_MODES = ("api", "cli")
AI_MODE = os.getenv("AI_MODE") if os.getenv("AI_MODE") in AI_MODES else "api"
# Each one is locked so it can only answer: no commands, no file edits. Aura gives them emails and web
# pages, which could hide instructions ("prompt injection"), so asking nicely in the prompt isn't enough.
CLI_TOOLS = {
    # --tools "" = no built-in tools; --strict-mcp-config with no --mcp-config = no connectors (Gmail, Notion...)
    "claude":      {"name": "Claude Code", "command": ["claude", "-p", "--tools", "", "--strict-mcp-config"], "stdin": True},
    "codex":       {"name": "Codex", "command": ["codex", "exec", "--skip-git-repo-check", "--ephemeral",
                                                 "-s", "read-only", "--color", "never", "-"], "stdin": True,
                    "image_flag": "--image="},
    # agy ignores plain stdin, so the prompt goes in as one JSON line ("stream": llm.py writes and reads it);
    # that keeps it out of the command line, which other programs on the PC can see. -p needs a value: "".
    # (--mode plan is not used: agy says it has no effect with slash commands off, and those must stay off.)
    "antigravity": {"name": "Antigravity", "command": ["agy", "--sandbox", "--disable-slash-commands",
                                                       "--input-format", "stream-json",
                                                       "--output-format", "stream-json", "-p", ""],
                    "stdin": True, "stream": True},
    "opencode":    {"name": "OpenCode", "command": ["opencode", "run"], "stdin": True,
                    "env": {"OPENCODE_CONFIG_CONTENT": json.dumps(
                        {"permission": {"edit": "deny", "bash": "deny", "webfetch": "deny"}})}},
}
CLI_TIMEOUT_SECONDS = 180  # CLIs start up and think longer than one API call
ORCHESTRATOR_CLI = os.getenv("ORCHESTRATOR_CLI") if os.getenv("ORCHESTRATOR_CLI") in CLI_TOOLS else "claude"


def agent_cli(agent):
    """The CLI an agent uses: its own AGENT_CLI_<NAME>, else the planner's. Pictures (vision) use the Reader's."""
    if agent == "vision":
        agent = "reader"
    own = os.getenv("AGENT_CLI_" + agent.upper())
    return own if own in CLI_TOOLS else ORCHESTRATOR_CLI

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
# Voice shortcut: hold to talk (a wave in the notch), let go to type it in the dock (Enter runs it)
VOICE_SHORTCUT = os.getenv("VOICE_SHORTCUT") or "Ctrl+Alt+V"
# "notch": a black notch stuck to the top edge of the screen (always top middle) instead of the glass pill
DOCK_STYLE = "notch" if os.getenv("DOCK_STYLE") == "notch" else "dock"

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
