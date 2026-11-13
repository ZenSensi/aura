# Aura: build context for AI coding agents

This file is the handover document for any AI agent (Antigravity, Codex, Claude Code, OpenCode, Cursor...)
that continues building Aura. Read it fully before changing anything. `README.md` is the user-facing
manual; this file is the developer-facing one: how the code fits together, the rules the owner has set,
and the traps already found the hard way.

- Repo: `https://github.com/ZenSensi/aura` (PUBLIC), branch `main`
- Website (GitHub Pages from `docs/`): `https://zensensi.github.io/aura/`
- Current version: **1.5.1** (`version_info.txt`), latest GitHub Release `v1.5.1`
- Written: 2026-11-13. If code and this file disagree, the code wins; fix this file.

---

## 1. What Aura is

Aura is a multi-agent desktop assistant for **Windows 10/11**. The user types or speaks a request; an
**Orchestrator** model turns it into a JSON plan of at most 5 steps; each step is run by one of **16
specialist agents** (write, email, open apps, WhatsApp, music, maths, web, files, reminders, Notion...).

It is the owner's BCA Data Science & AI college project, so it must stay **explainable in a viva**:

- **Plain Python, no agent framework** (no LangChain, CrewAI, AutoGen...). Every agent is one small module.
- **Native desktop app**: pywebview window over local HTML/CSS/JS. **No browser UI, no web server, no
  open port.** (A Flask server and a Streamlit UI existed once and were removed on purpose. Do not bring
  them back. `.streamlit/config.toml` is a leftover with no code using it.)
- **Safety is done by Python, not by the model**: the model only picks names and fills forms; Python
  checks every action that cannot be undone.
- Uses pre-trained models through APIs. Nothing is trained.

Code style to keep: small readable functions, a docstring at the top of every file explaining it in plain
English, comments that say *why*, constants in CAPS at the top of the file, no clever abstractions.

---

## 2. Quick start

```powershell
python -m venv venv ; venv\Scripts\activate
pip install -r requirements.txt        # versions are pinned with == on purpose
copy .env.example .env                 # then add keys, or fill them in Settings inside the app

python desktop.py                      # the desktop app (use this while developing)
python desktop.py --minimized          # as "Start with Windows" launches it: hidden, tray + dock only
python main.py                         # terminal version (kept for the viva demo)
python run_tests.py                    # 33 planner tests, dry run (see section 12 before running)
python -m agents.whatsapp              # self-check of the contact parser
```

Python 3.12 is what it is developed on (3.10+ works). Only Windows is supported: the dock, notch, voice,
music, WhatsApp, tray and hotkeys all use Win32/WinRT through `ctypes` and PowerShell.

Only one Aura can run (named mutex `Local\AuraAssistantSingleInstance`). A second `python desktop.py`
exits at once, so close the running copy (tray icon > Quit Aura) before starting your own.

---

## 3. Architecture

```
 static/index.html + app.js   (main window)        static/dock.html + dock.js   (dock / notch)
            |  window.pywebview.api.<method>()            |
            v                                             v
        desktop.py  class Api  (the ONLY bridge between JavaScript and Python)
            |  run(text, options)
            v
        orchestrator.run_request()   <- a GENERATOR that yields events
            |  1. make_plan()  -> llm.call_llm_json("orchestrator", ...) -> validate_plan()
            |  2. for each step: AGENTS[agent](task, state)
            v
        agents/<name>.py  run_<name>(task, state) -> result dict
            |
        llm.py (API or CLI engines, fallback chain)   config.py (every model/limit/allowlist)
        logger.py (one JSON line per step in logs/run_log.jsonl)
```

### 3.1 The request flow

1. JS calls `api.run(text, options)`. `options` may hold `web` (Web search on), `attach` (use the picked
   file), `agent` (the pinned agent), `routine` (a scheduled routine started it).
2. `Api.run` takes a lock (one request at a time), then loops over `run_request(...)`.
3. Every yielded event is pushed to the page with `window.evaluate_js("onAgentEvent({...})")`, and to the
   dock with `onDockEvent({...})` when the dock is visible.
4. When the generator ends, `notification()` builds a "task done" card and `Api._notify` shows it.

`main.py` loops over the same generator and prints the events. Agent logic is written once.

### 3.2 Events yielded by `run_request`

| `type` | Extra fields |
|:--|:--|
| `planning` | none |
| `plan` | `steps` `[{agent, task}]`, `reply` (text when there are no steps), `model`, `seconds`, `fallback_used`, `workflow` |
| `step_start` | `number`, `total`, `agent`, `task` |
| `step_done` | the same plus `result` (the agent's result dict) |
| `step_failed` | the same plus `message`; the request stops here |
| `error` | `message` (planning itself failed) |
| `done` | `ok`, `nothing_to_do`, `reply` |

### 3.3 The shared `state` dict (one per request)

| Key | Set by | Used by |
|:--|:--|:--|
| `request` | orchestrator (what the user typed, or the workflow's saved request) | Mailer (recipient addresses), WhatsApp (contact name, exact words), Chat/Tutor |
| `last_file`, `last_type`, `last_source` | Writer (`"writer"`), Files (`"files"`) | System ("open the last file"), Mailer, WhatsApp |
| `last_answer` | Calc, Chat, Tutor, Web, Reader, News, Inbox, Notion | Writer (note with the answer), WhatsApp, Notion |
| `reply_to` | Inbox (the email it found) | Writer + Mailer (reply in thread) |
| `attachment` | orchestrator (this request's file, or the previous one's) | Reader |
| `history` | orchestrator (last 12 turns of this chat) | Chat, Tutor |
| `previous` | orchestrator (copy of the caller's `memory`) | WhatsApp ("send this answer") |
| `dry_run` | caller (`run_tests.py`) | every agent that acts: decide but do nothing |
| `confirm` | caller: `confirm(kind, to, text) -> bool` | Mailer, WhatsApp (ask before sending) |
| `user_name` | config | Chat, Writer |

`memory` is a dict the caller keeps between requests (`Api._memory`): `request`, `last_answer`,
`last_file`, `attachment`, `history`. The "New chat" button clears it.

### 3.4 The agent contract

```python
def run_<name>(task: str, state: dict) -> dict:
    return {
        "agent": "<name>",
        "model": reply["model"],            # or "none (plain Python)"
        "seconds": 1.23,
        "fallback_used": False,
        "action": {"action": "<verb>", "target": "<what>"},   # "refuse" as the verb = shown as Refused
        "message": "One short line for the card, dock and notification",
        # optional extras the UI knows: "answer", "sources" [{title, url}], agent-specific fields
    }
```

The Writer is the one exception: it returns `type`, `file`, `content` instead of `action`/`message`.
To fail, raise `LLMError`, `ValueError` or `OSError` with a message written for the user: the
orchestrator shows it, logs it, and stops the request. Any other exception is shown as a generic
"Something went wrong in the X agent".

---

## 4. File map

| File | Lines | What it holds |
|:--|--:|:--|
| `desktop.py` | 834 | App entry. `class Api` (js_api bridge: run, settings, dock, notch, voice, automations, Google), window creation, tray, single-instance mutex, `confirm_send` message box, "Start with Windows" registry value |
| `orchestrator.py` | 412 | `SYSTEM_PROMPT` (the planner rules), `AGENTS` registry, `make_plan`, `validate_plan`, `run_request`, agent on/off (`agents_off.json`) |
| `llm.py` | 278 | `call_model` (one OpenAI-compatible call), `call_cli` (coding-agent CLI as the model), `call_llm` (fallback chain), `extract_json`, `call_llm_json`, `USAGE` counters, `on_fallback` hook |
| `config.py` | 301 | **The only file that names a model, URL, limit or allowlist.** `BASE_DIR`, `save_env`, `PROVIDERS`, `AGENT_MODELS`, `CLI_TOOLS`, `LAST_RESORT`, dock settings, app/site allowlists and block lists |
| `logger.py` | 120 | `log_step`, `read_log`, `usage()` (per-agent counts), `recent()` (Recent tasks). Private agents log only text lengths |
| `automations.py` | 463 | `automations.json` store (routines, reminders, workflows), schedule maths (`build_schedule`, `next_time`), `Scheduler` thread (10 s tick) |
| `google_sync.py` | 212 | Google OAuth (Tasks + Calendar token, separate Gmail token), `add_to_phone`, `upcoming_events`, `gmail()` REST helper |
| `dock.py` | 713 | `class Dock`: the floating pill / notch window, live glass, shape cutting, grey-box healing, timer, banners, HUD |
| `notch.py` | 483 | Notch data: weather, calendar, battery, mute, `KeepAwake`, lock, Do Not Disturb, `Hud` (volume/brightness), `Devices` (charger/Bluetooth), `Clipboard` shelf |
| `notify.py` | 199 | `class Notifier` (top-right glass card) and synthesized chimes (`play_chime`) |
| `hotkey.py` | 189 | Global shortcuts: `RegisterHotKey`, plus a low-level keyboard hook for the default `` ` `` + Space |
| `tray.py` | 25 | System tray icon (pystray): Open Aura / Settings / Dock settings / Quit Aura |
| `voice.py` | 168 | Mic recording through Windows MCI, transcription with Groq Whisper |
| `nowplaying.py` | 146 | Long-running PowerShell that reads Windows media sessions (title, artist, cover) for the dock |
| `main.py` | 80 | Terminal version |
| `run_tests.py` | 158 | 33 labelled requests, dry run, writes `logs/test_results.csv` |
| `agents/*.py` | 66-379 each | One agent per file (section 5) |
| `static/index.html`, `style.css`, `app.js` (1696), `motion.css` | | Main window UI |
| `static/dock.html`, `dock.css`, `dock.js` (772) | | Dock / notch UI |
| `static/notify.html`, `notify.css`, `notify.js` | | Notification card |
| `static/rich.js` | | Markdown + maths renderer (marked + DOMPurify + KaTeX) |
| `static/icons.js`, `voice.js` | | SVG icons; mic button logic |
| `static/vendor/` | | marked 12.0.2, DOMPurify 3.4.16, KaTeX 0.16.47, stored locally so Aura works offline (`VERSIONS.md`) |
| `assets/` | | Logo and icons. `make_logo.py` draws them all (SVG, PNG, ICO) |
| `docs/` | | The public website: `index.html`, `privacy.html`, `terms.html`. **A push to `main` publishes it** |
| `build_exe.bat`, `version_info.txt` | | PyInstaller build and the version baked into the exe |

---

## 5. The agents

| Agent | File | AI? | Notes that matter when editing |
|:--|:--|:--|:--|
| Writer | `writer.py` | yes | Model returns JSON `{title, type, content}`; saved to `outputs/<title>_<time>.txt`. `exact_words()` saves quoted words with no model, only if they appear word for word in the request. `as_data()` fences untrusted text. Emails must start with `Subject:` |
| System | `system.py` | picks a name only | Actions `open_app`, `open_site`, `open_last_file`, `refuse`. Reads the Start Menu for installed apps; Python finds the command. Blocks by name, target and package (config). Never opens `RUNNABLE` file types found by Files |
| Mailer | `mailer.py` | **no** | Sends only to addresses the user typed (or the `reply_to` sender), only this request's email, refuses `[placeholders]`, max 3 recipients. Gmail API when connected, else SMTP with certificate check |
| Inbox | `inbox.py` | yes | Plan JSON then fetch (Gmail API if connected, else IMAP read-only), then summarise. Manage actions (archive, trash, read/unread, star, label) need Connect Gmail, touch only the emails found, max 10. Never deletes forever, never forwards |
| Files | `files.py` | plans the search | Walks Desktop/Documents/Downloads/Pictures/Music/Videos (and OneDrive), read-only, 8 s and 300k-file limits |
| News | `news.py` | yes | Google News RSS + Yahoo Finance chart API. Returns headlines and market series for the charts |
| Calc | `calc.py` | **no** | `ast`-based exact arithmetic. `18% of 2450` = 441; `17 % 5` stays modulo |
| WhatsApp | `whatsapp.py` | **no** | Opens `whatsapp://send?phone=`, **types** the text with `SendInput` (WhatsApp Desktop ignores `text=`), checks WhatsApp is the front window before every key, up to 3 refocus tries, else refuses. Contacts only from `contacts.json` and only a name the user typed |
| Web | `web.py` | yes | Tavily search then answer with sources. "latest/recent" queries add a time range and the month |
| Reader | `reader.py` | yes | PDF (PyMuPDF), `.docx`, text, images. Images and scanned PDFs go to the `vision` model, which has **no** free last-resort backup (privacy) |
| Chat | `chat.py` | yes | Conversation using `state["history"]`. Plain text, no markdown |
| Tutor | `tutor.py` | yes | `reasoning_effort: high`. Markdown + LaTeX answer drawn by `rich.js`. `first_line()` makes the LaTeX-free line for notifications |
| Automate | `automate.py` | fills a form | Model returns `{action, name, task, when...}`; **Python works out the times**. Every reminder goes to the PC and, if Google is connected, to Google Tasks/Calendar |
| Organiser | `organiser.py` | **no** | Moves loose files into type folders, never deletes, skips files changed in the last 3 minutes, undo via `logs/organise_undo.json` |
| Music | `music.py` | **no** | YouTube Music search (keyless) gives the real title, then Tavily finds the Spotify track id, then `spotify:track:ID`. Control (pause/next...) through Windows media sessions, never keystrokes |
| Notion | `notion.py` | yes | REST API with `NOTION_TOKEN`. Search, read, append. Never edits or deletes existing content |

The planner's routing rules for all of these live in `orchestrator.SYSTEM_PROMPT`. When a request goes
to the wrong agent, fix the rule there and add a line to `run_tests.py`.

---

## 6. The model layer

- **Providers** (`config.PROVIDERS`): Groq, Gemini, OpenRouter. All speak the OpenAI chat API, so
  `llm.call_model` is one `requests.post` to `<base_url>/chat/completions`.
- **Per-agent routing** (`config.AGENT_MODELS`): each entry has `provider`, `model`, optional `api_key`
  (a second Groq key gives Orchestrator/System/Files/Inbox/Notion their own rate limit), optional
  `options` merged into the request body, a `fallback` on a different provider, optional
  `no_last_resort`.
- **Chain** in `llm.call_llm`: main -> fallback -> `LAST_RESORT` (`openrouter/free`) -> the agent's CLI
  (if installed). With `AI_MODE=cli` the CLI goes first, then the API models that have a key.
- **CLI engines** (`config.CLI_TOOLS`): Claude Code, Codex, Antigravity (`agy`), OpenCode used as a plain
  text model. Each runs in an empty temp folder under `cli_work/`, with its tools and connectors switched
  off and with Aura's `.env` secrets removed from its environment (`llm._cli_env`). Every prompt goes in
  on stdin, never on the command line. `agy` ignores plain stdin, so it gets one JSON line
  (`--input-format stream-json`, `"stream": True` in its entry; `llm._stream_reply` reads the answer).
  `agy --mode plan` has no effect while slash commands are disabled, and they must stay disabled.
- **JSON**: `call_llm_json` strips code fences, takes first `{` to last `}`, and re-asks once on bad JSON.
- **Settings apply live**: `config.save_env` writes `.env`, sets `os.environ` and reloads the `config`
  module. This only works because every file reads `config.X` at call time. **Never write
  `from config import X`** for a value that Settings can change.
- Model names go stale often. A "model not found" error means: update the name in `config.py` only.

Current models: `openai/gpt-oss-120b` (most agents), `qwen/qwen3.8-27b` (System, vision),
`openai/gpt-oss-20b` (Files), `gemini-3.5-flash` / `gemini-3.5-flash-lite` (backups),
`whisper-large-v3-turbo` (voice).

---

## 7. The UI

Three pywebview windows. The main window gets the whole `Api` object as its `js_api`; the dock and the
notification get only the functions their pages call (`desktop.only(api, DOCK_API)` / `NOTIFY_API`).
**A new `api().something()` call in `dock.js`, `notify.js` or `voice.js` must be added to those lists.**

1. **Main** (`index.html`): sidebar (New chat, Automations, Agents, Recent tasks, Settings, theme),
   chat, composer with + menu (attach, web search, open an app) and mic.
2. **Dock** (`dock.html`, title `Aura Dock`): frameless, always on top, shown while the main window is
   minimised or hidden. Two styles: glass **pill** (296x40, six positions) or black **notch** (top
   middle; 200x46 resting, 360x46 when showing a song, work, timer or HUD).
3. **Notification** (`notify.html`, title `Aura Notification`): glass card at the top-right of the screen.

Rules for the JS side:

- Python -> page: `window.evaluate_js("fn(json)")`. Page -> Python: `await window.pywebview.api.method()`.
  Methods whose name starts with `_` are left out of `window.pywebview.api` (pywebview's raw bridge can
  still reach any attribute by name, so that is tidiness, not a security wall; the pages are local files
  under a strict CSP).
- **Secrets never reach the page.** `get_settings` returns only whether each key is set. The page gets
  file *names*, not paths. `open_link` only opens links Aura itself produced (`Api._links`).
- All model text is rendered through `rich.js` (DOMPurify, `id`/`name` forbidden, KaTeX limits). Never
  put model output into `innerHTML` directly.
- A strict Content-Security-Policy is set in the HTML files: scripts only from Aura's own files
  (`script-src 'self'`), no network calls from the page (`connect-src 'self'`); the only outside source
  allowed is Google Fonts for styles and fonts. Add libraries to `static/vendor/` and record them in
  `VERSIONS.md`.
- The agent list (name, icon, colour, description) is duplicated in `app.js` (`AGENTS` near line 17) and
  `dock.js` (near line 16), with colours as CSS variables in `style.css` (dark and light blocks).

### 7.1 Dock internals worth knowing before touching `dock.py`

- The dock window **never resizes** (created 660x640). Its visible shape is cut with `SetWindowRgn`;
  `dock.js` animates the body with CSS and calls `api.dock_shape(w, h, r, seq)` first. `seq` exists
  because pywebview calls can arrive out of order.
- "Glass" is a blurred screenshot of what is behind the dock, grabbed with `BitBlt` while
  `WDA_EXCLUDEFROMCAPTURE` is on, refreshed 15 times a second while the background moves and twice a
  second when still.
- `SetWindowRgn` has no anti-aliasing: a 2px CSS ring showing the unblurred screenshot hides the jagged
  edge. The Win11 system backdrop must be set to NONE or a dark box shows behind the corners.
- **Never read a WinForms `.Handle` from a worker thread without checking `IsHandleCreated`**: it
  re-creates the window on that thread, which has no message loop. That was the root cause of a hung
  grey box and a Quit deadlock (fixed in `6be8733`; guards are in `dock._hwnd`, `notify._hwnd`,
  `desktop.style_title_bar`).
- `_heal_loop` (own thread, every 2 s): watchdog for a hung GUI thread (dumps stacks to `logs/hang.log`),
  and grey-box detection (3 screen pixels equal to `#202020`) followed by `_repair` (re-cut + frame
  change, then hide/show at most once per 30 s). Every repair is logged to `logs/dock.log`.
- `#202020` is also the exact background of Windows' dark-mode apps, so a dark window behind the dock
  looks like a grey box to that check. `_grey_behind` looks again with the dock left out of the capture
  and the log line ends with where the grey came from: "the dock itself" (a real fault) or "a dark
  window behind it" (harmless; no hide/show, one log line per 10 minutes).

---

## 8. Automations, Google, Gmail

- `automations.json` holds `routines`, `reminders`, `workflows`. The `Scheduler` thread ticks every 10 s.
  A routine is run by calling the page's `runRoutine()`, so it appears in the chat like a typed request,
  with `automated=True` (it cannot create more automations) and **without** the confirm box.
- Shortest repeat is 15 minutes; 50 items of each kind; a routine missed by more than 30 minutes waits
  for its next time.
- Typing a workflow's name runs its saved request (`automations.find_workflow`).
- Google OAuth uses the Desktop-app flow. `google_client.json` is **gitignored and bundled into the exe
  at build time**; a user's own file in the data folder wins. Two separate tokens:
  `google_token.json` (Tasks + Calendar events) and `gmail_token.json` (`gmail.modify`, "Connect Gmail"
  button). They are separate on purpose so the simple consent stays easy to approve.
- The Tasks API drops the time, so a timed reminder = a dated task + a Calendar event with a pop-up.

---

## 9. Where data lives

| | Running from source | `Aura.exe` |
|:--|:--|:--|
| `BASE_DIR` | the repo folder | `%LOCALAPPDATA%\Aura`, unless a `.env` sits next to the exe ("portable" mode; the owner's PC runs this way from the repo folder) |

In `BASE_DIR`, all gitignored: `.env`, `contacts.json`, `google_client.json`, `google_token.json`,
`gmail_token.json`, `automations.json`, `agents_off.json`, `focus_dnd.mark`, `cli_work/`,
`outputs/*.txt`, `logs/*.jsonl`, `logs/*.log`, `logs/organise_undo.json`. Build output (`build/`,
`dist/`, `*.exe`, `*.spec`) is ignored too.

`.env` names: `GROQ_API_KEY`, `GROQ_API_KEY_SYSTEM`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`,
`TAVILY_API_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `USER_NAME`, `NOTION_TOKEN`, `AI_MODE`,
`ORCHESTRATOR_CLI`, `AGENT_CLI_<AGENT>`, `DOCK_POSITION`, `DOCK_SHORTCUT`, `DOCK_AUTOHIDE`,
`DOCK_STYLE`, `VOICE_SHORTCUT`.

Secrets (`config.SECRET_NAMES`: the API keys, the Gmail App Password, the Notion token) are stored
locked with Windows DPAPI as `dpapi:<base64>`: `config.save_env` locks on save, `config._unlock_env`
puts the real value in `os.environ` at start, and the exe (not a run from source) also locks plain
values it finds. A plain value still works. **An older Aura.exe (1.5.1 or earlier) cannot read a locked
`.env`.** Always read keys through `config.X` / `os.environ`, never from the file. A new secret must be
added to `SECRET_NAMES`.

A new setting needs: a line in `config.py`, an entry in
`desktop.SETTINGS` (or `set_dock` / `set_ai`), a row in `app.js`, and a line in `.env.example`.

---

## 10. Safety rules that must not be weakened

1. **Ask before sending.** Email and WhatsApp call `state["confirm"]` (a Yes/No box, No by default, no
   answer in 5 minutes = No). Only routines the user set up send without asking.
2. **Mailer** sends only to addresses found in the user's own typed request, or to the sender of the
   email the Inbox found. Never to an address that came from a model, an email body or a web page.
3. **WhatsApp** sends only to a contact the user named, from `contacts.json`, and presses a key only
   while WhatsApp is the front window.
4. **System**: the model picks a name; Python resolves the command from an allowlist or the Start Menu.
   Shells, registry, script hosts and system tools are blocked by name, target and package. The model
   never writes a command.
5. **Untrusted text is data.** Emails, files, web pages, headlines, Notion pages and chat history are
   fenced in tags with `<` and `>` neutralised, and prompts say to ignore instructions inside them.
6. **CLI engines stay locked** (no tools, no connectors, no Aura secrets in their environment).
7. **Organiser moves, never deletes**, and records every move for undo. Inbox trashes at most; it never
   deletes forever or forwards.
8. **Logs hold no private text** for Writer, Mailer, WhatsApp, Inbox, Reader, Notion (only lengths), and
   the plan log holds agent names only.
9. `contacts.json` is never sent to a model. Free vision endpoints are never used for the user's images.
10. **The repo is public.** Before every commit, check staged files for keys, tokens, real email
    addresses and phone numbers. Never commit `google_client.json`.

---

## 11. How to add an agent (checklist)

1. `agents/<name>.py` with `run_<name>(task, state)` following section 3.4. Honour `state["dry_run"]`.
   Set `state["last_answer"]` if a later step could use the result.
2. `orchestrator.py`: import it, add it to `AGENTS`, describe it in `SYSTEM_PROMPT` (the agent list,
   a routing rule, and the JSON schema line at the bottom).
3. `config.py`: an `AGENT_MODELS` entry if it calls a model.
4. `logger.py`: add it to `PRIVATE_AGENTS` if it handles private text.
5. `static/app.js`: an `AGENTS` entry, and a branch in the `step_done` rendering if it needs its own card.
   `static/dock.js`: the same entry. `static/style.css`: a `--<name>` colour in both theme blocks
   (and in `dock.css` if the dock uses it).
6. `desktop.py`: `notification()` if the default summary line is not right; `Api._links` if it returns
   source links that should be clickable.
7. `run_tests.py`: at least one labelled request. Update the count in its docstring and in `README.md`.
8. `README.md`: the Agents table, the count ("16 agents"), the things-to-try table.
9. `agents/chat.py`: its "what can you do" line.

---

## 12. Testing rules

- `python run_tests.py` makes **about 33 real model calls** on free-tier keys. Do not run it casually; a
  full run late in the day often fails from test 14 onward with 429s that are not code bugs. It is a dry
  run, but its requests appear in Recent tasks and in the usage counts.
- Expected result today: 32/33. The Notion test fails on the owner's PC because no Notion page is shared
  with the integration yet. The WhatsApp test (#14) is sometimes planned differently.
- **The owner uses the PC while you work. Never send real mouse clicks or keystrokes.** Keystrokes have
  landed in the wrong window before (once into a terminal as a fake message, once restoring an unsaved
  draft in Notepad). Drive the UI through a probe script that wraps `webview.start(func)` and calls
  `window.evaluate_js(...)`; for typing tests use a throwaway Tk window.
- Never send a real email or WhatsApp message, never run a real (non-preview) tidy-up of Downloads, and
  never turn on "Start with Windows" in a test. After UI tests, check `automations.json` for leftovers
  and that `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` has no `Aura` value unless the owner set it.
- Static rendering can be checked in headless Edge with a stub `window.pywebview`; add
  `*{transition:none}` or headless freezes mid-fade. Animations need a real window.
- Screenshots sometimes miss the dock: the glass grab hides it from captures for a moment. Take several,
  or pause the glass loop in your harness.
- A onefile exe is two `Aura.exe` processes (unpacker + app); the windows belong to the child.
  `py-spy dump --pid <child pid>` works on it and is the fastest way to diagnose a hang.

---

## 13. Build and release

**Build** (the exe cannot be overwritten while Aura runs: ask the owner to quit it from the tray first,
never kill their running copy):

```powershell
cmd /c ".\build_exe.bat < nul"                       # writes Aura.exe into the repo folder
python -m PyInstaller --noconfirm Aura.spec          # alternative: writes dist\Aura.exe
```

- The build stops if `google_client.json` is missing (it is bundled with `--add-data`).
- `static/` and `assets/icon.ico` are bundled; read them through `desktop.resource_path()`.
- The `--exclude-module` list keeps PyMuPDF's optional AI add-on (torch, scipy...) out. Without it the
  build takes over 10 minutes and the exe balloons. The exe is about 61 MB.
- From Git Bash use forward slashes in `--icon`; `cmd //c build_exe.bat` does not work there.
- "Permission denied ... Retrying" lines while appending the package are harmless.

**Release** (only when the owner asks for one):

1. Bump the four version numbers in `version_info.txt`; commit as `Version X.Y.Z`.
2. Build, start the exe once, check it holds no private files.
3. Zip `Aura.exe` alone as `Aura_Windows_x64.zip`, note its SHA-256.
4. `gh release create vX.Y.Z` with the zip (and the bare `Aura.exe`), mark it Latest, keep older releases.
5. Write release notes with Bash, not PowerShell (PowerShell eats backticks).

**Git rules set by the owner:**

- Commit and push only when asked. Commit as the owner only: **no `Co-Authored-By` or "Generated with"
  lines** in commits, PRs or release notes.
- Commit messages are one plain-English sentence about what changed for the user
  (see `git log --oneline`).
- Do not rewrite history or force-push.

---

## 14. Decisions the owner has already made (do not reopen)

- Desktop app only; no browser UI, no web server.
- Keep the purple **aurora** theme. A black/blue "Horizon" theme was tried and rejected. Ask before any
  theme change and put trials in a separate, removable CSS file (as `motion.css` is).
- The glass **dock pill stays as it is** (296x40 live glass). Restyles were tried and all reverted.
- The dock and notification windows cannot be dragged (`easy_drag=False`).
- Notch style: all alerts appear in the notch. Dock style: alerts use the top-right glass card.
- The closed notch shows two blinking eyes and a bottom glow while idle; the owner disliked a bar wave.
- Every reminder goes to both the PC and the phone (Google), without having to say "phone".
- Microsoft To Do was rejected for phone reminders; Google Tasks + Calendar was chosen.
- The window's X hides Aura to the tray and dock; only tray > Quit Aura closes it.
- Logo: white "aurora ribbon A" on the accent gradient tile. Regenerate with `assets/make_logo.py`.
- Example chips are templates the user completes ("Play  on "), not finished requests.
- The Jupyter notebook and the CLINC150 dataset were removed on purpose. Do not add them back.
- `main.py` stays (viva demo).

---

## 15. Traps already found (read before debugging)

**Windows / pywebview**
- pywebview frameless windows come out about 16x39 px smaller than asked. Measure, do not assume.
- `transparent=True` in pywebview force-shows hidden windows and does not give transparent corners.
- `SetWindowPos` with insert-after `-1` must be `wintypes.HWND(-1)` on 64-bit.
- `SetForegroundWindow` often fails from a script; `AttachThreadInput` or a topmost toggle is needed.
- `window.hide()` waits for the GUI thread: call it from another thread inside event handlers.
- A hidden window sends no "restored" event, so `dock_open_main` calls `dock.on_restored()` itself.
- PowerShell variable names ignore case (`$p` and `$P` are the same variable).
- In Windows PowerShell, read stdin with `StreamReader(OpenStandardInput())`; `Console.In.ReadLineAsync` blocks.
- MCI recordings belong to the thread that started them and reject long save paths (use `%TEMP%`).

**CSS in WebView2**
- An element with `backdrop-filter`, or an animation with fill mode `both`, becomes a "backdrop root":
  glass inside it cannot blur the page and turns flat. Put the blur on a `::before` layer and use fill
  mode `backwards`.
- `scrollbar-width: thin` makes WebView2 ignore `::-webkit-scrollbar` styles.
- `[hidden]` loses to any rule that sets `display`; there is an `!important` rule for it.
- Native `<select>` pop-ups need explicit `option` colours in both themes.

**Models**
- `gpt-oss` writes maths as `\( \)` and `\[ \]` whatever the prompt says, and cites as `【n】`. The
  renderer and `web.tidy_answer` handle both.
- `qwen3.8` stops after one word if told only "plain text only"; keep "answer in complete sentences".
- Groq Whisper's `no_speech_prob` is always 0; silence is detected with loudness and `avg_logprob >= -0.8`.
  A vocabulary `prompt` makes Whisper echo it on silence.
- Groq's free tier is 8000 tokens a minute: rapid testing falls back to Gemini, which is normal.
- Keyless web search (DuckDuckGo, Bing, Mojeek) does not work from code; Tavily is required.

**WhatsApp Desktop**
- It ignores `text=` in `whatsapp://send` links, so the text is typed, with pauses between select-all,
  delete, typing and Enter. Delivery cannot be verified from code.

---

## 16. Current state (2026-11-13, late evening)

- `main` is pushed and matches `origin/main`. The newest work is not in any release (latest is `v1.5.1`):
  the grey-box detect-and-repair (`a31d95d`), and after it: locked keys in `.env` (DPAPI), the dock and
  notification windows limited to their own functions, Antigravity's prompt on stdin, Reader limits for
  huge Word files and PDFs, contact-name lookup through Connect Gmail, the grey-box log that names the
  cause, this file and `CLAUDE.md`. All tested as listed below.
- `dist\Aura.exe` is built from that work. `Aura.exe` in the repo folder (the copy the owner runs) is
  still the older build from `a31d95d` until the owner quits Aura and swaps it.
- 33 planner tests; last full run 32/33 (Notion, see section 12). Not re-run after these changes
  (nothing in planning changed); two dry-run requests were run end to end.

**Tested for real on 2026-11-13**

- Gmail through Connect Gmail on the owner's account, on one test email the test placed in the inbox
  itself: read (stays unread), star, unstar, mark read, mark unread, label (new label), archive, trash,
  and the whole Inbox agent with the real model planning a "star" request. All passed.
- The Yes/No confirm box: No returns False, Yes returns True (answered by window message, no email involved).
- Antigravity as an engine with the prompt on stdin: answers.
- A hidden pywebview window given two functions: its page sees and can call only those two.

**Open items**

1. **Notch grey box.** 33 show/hide and cold-start rounds of a probe notch (repair switched off) never
   produced one, so there is no reproduction. Windows apps are in dark mode on the owner's PC, and
   dark apps are exactly the grey the detector looks for, so an unknown share of the 33 logged
   "repairs" were dark windows behind the notch, not faults. From this build on, each line in
   `logs/dock.log` says which it was. **Next step: after a few days of use, read `logs/dock.log`.**
   Only lines ending "the dock itself" are real; if there are none, the problem is gone and the
   suspicion about `SetWindowDisplayAffinity` can be dropped.
2. **A real email send through the Gmail API has still never been run** (the code path is the same
   `messages/send` call for new mail and replies). It needs the owner to ask for one from Aura and
   click Yes.
3. **Google OAuth verification.** The app is published but unverified (warning screen, 100-user cap).
   Google's branding check rejected it twice: "homepage not registered to you" and "logo does not
   identify the brand", although Search Console ownership is verified. Options: remove the logo from
   the consent screen, or request a manual review. The owner has to pick one. Gmail's scope is
   restricted, so full verification would need a paid security assessment; it is not planned.
4. **Needs the owner**: Codex's sign-in has expired (`codex login`), OpenCode cannot reach its provider
   (its own setup), and no Notion page is shared with the integration yet (0 pages), so those three
   stay untested. A real sleep/wake cycle and recording a new shortcut by pressing keys can only be
   checked by the owner.
5. **Still plain text on disk**: `google_token.json` and `gmail_token.json` (Google sign-in tokens).
   The `.env` secrets are locked; these could be locked the same way.
6. The exe is unsigned, so Windows SmartScreen warns on first run. Fixing that needs a paid
   code-signing certificate.

**Version history**

| Version | What it added |
|:--|:--|
| 1.0.0 | First release: orchestrator, 16 agents, dock, notifications, automations, Settings, per-user data folder |
| 1.1.0 | UI polish, separate Agents panel |
| 1.2.0 | Dock shortcut (`` ` `` + Space), six positions, autohide |
| 1.3.0 | System tray, quiet start with Windows |
| 1.4.0 | Notch style and its features, AI engine choice (API or CLI), bundled Google sign-in, security review, push-to-talk, focus timer with Do Not Disturb |
| 1.5.0 | Connect Gmail (read, send, manage through Google sign-in), ask before sending |
| 1.5.1 | Grey-box root-cause fix, louder reminder alarm |

---

## 17. Working agreement for the next agent

- Make the smallest change that fully does the job, in the style of the file you are in.
- After a code change: run the app from source and check the change; rebuild the exe only when the
  owner wants to use it (and only after they have quit Aura).
- Keep `README.md`, `docs/privacy.html` (if what leaves the PC changes) and this file in step with the code.
- When unsure whether something is a deliberate decision, check sections 10 and 14, then ask.
