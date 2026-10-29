"""
app.py - Web chat UI for Aura (built with Streamlit).

Run:  streamlit run app.py

How it works:
  - The user types a request in the chat box.
  - orchestrator.run_request() yields events (planning, plan, step_start...).
  - show_event() draws each event on screen as soon as it arrives, so you
    can watch every agent work live.
  - The events are saved in st.session_state, so the chat history is
    redrawn with the same show_event() function after every click.
"""

import html
import os

import streamlit as st

import config
from orchestrator import run_request
from agents.system import open_file

st.set_page_config(page_title="Aura", page_icon="🤖", layout="centered")

# Icon, display name and short description for each agent
AGENT_INFO = {
    "orchestrator": ("🧠", "Orchestrator", "Plans the steps"),
    "writer":       ("✍️", "Writer", "Letters, emails & messages"),
    "system":       ("🖥️", "System", "Opens apps, sites & files"),
    "mailer":       ("📤", "Mailer", "Sends emails via Gmail"),
    "inbox":        ("📥", "Inbox", "Reads & searches Gmail"),
    "files":        ("🔎", "Files", "Finds files on this PC"),
    "news":         ("📰", "News", "Daily news & market charts"),
    "calc":         ("🧮", "Calc", "Exact maths (no AI)"),
    "whatsapp":     ("💬", "WhatsApp", "Sends to saved contacts"),
    "web":          ("🌐", "Web", "Searches the web"),
    "reader":       ("📄", "Reader", "Reads files & images"),
    "chat":         ("💭", "Chat", "Conversation"),
    "tutor":        ("🎓", "Tutor", "Maths, science & code"),
    "automate":     ("⏰", "Automate", "Reminders & routines"),
    "organiser":    ("🗂️", "Organiser", "Tidies folders"),
    "music":        ("🎵", "Music", "Spotify & YouTube Music"),
    "notion":       ("📓", "Notion", "Your Notion pages"),
}

EXAMPLES = [
    "✉️ Write an email to my teacher asking for an extension, then open Gmail",
    "📝 Write a leave letter for 2 days and open it",
    "🎂 Write a birthday message for my friend Riya",
    "🧮 Open the calculator",
]

# ---------------------------------------------------------------
# Styling (plain CSS injected into the page)
# ---------------------------------------------------------------
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
html, body, [class*="st-"], .stMarkdown, button, input, textarea { font-family: 'Inter', sans-serif; }
.block-container { padding-top: 2.2rem; max-width: 860px; }

.hero { text-align: center; padding: .6rem 0 1.2rem; }
.hero .logo { font-size: 2.6rem; line-height: 1; }
.hero h1 {
  font-size: 2.5rem; font-weight: 800; margin: .3rem 0 0; padding: 0; letter-spacing: -0.02em;
  background: linear-gradient(90deg, #6366f1, #a855f7, #ec4899);
  -webkit-background-clip: text; background-clip: text; -webkit-text-fill-color: transparent;
}
.hero p { opacity: .65; margin: .35rem 0 0; font-size: 1.02rem; }

.welcome {
  border: 1px solid rgba(128,128,128,.18); border-radius: 18px; padding: 1.1rem 1.3rem;
  background: linear-gradient(135deg, rgba(99,102,241,.07), rgba(236,72,153,.05));
  margin-bottom: .9rem;
}
.welcome h3 { margin: 0 0 .3rem; font-size: 1.1rem; }
.welcome p { margin: 0; opacity: .75; font-size: .93rem; }

.badge { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: .74rem; font-weight: 600; }
.badge-writer { background: rgba(139,92,246,.15); color: #8b5cf6; }
.badge-system { background: rgba(59,130,246,.15); color: #3b82f6; }

.plan-step {
  display: flex; gap: .7rem; align-items: center; padding: .55rem .75rem; margin-bottom: .4rem;
  border-radius: 12px; background: rgba(99,102,241,.07); border: 1px solid rgba(99,102,241,.12);
}
.plan-num {
  width: 26px; height: 26px; border-radius: 50%; flex-shrink: 0; display: flex; align-items: center;
  justify-content: center; font-size: .8rem; font-weight: 700; color: #fff;
  background: linear-gradient(135deg, #6366f1, #a855f7);
}
.plan-task { font-size: .92rem; }
.meta { font-size: .78rem; opacity: .6; margin-top: .2rem; }

.agent-card {
  display: flex; gap: .7rem; align-items: center; padding: .65rem .8rem; margin-bottom: .5rem;
  border-radius: 14px; border: 1px solid rgba(128,128,128,.2);
}
.agent-card .icon { font-size: 1.4rem; }
.agent-card b { display: block; font-size: .92rem; }
.agent-card small { opacity: .65; font-size: .75rem; display: block; }
.agent-card code { font-size: .7rem; }
.chip {
  display: inline-block; padding: 3px 10px; margin: 0 4px 6px 0; border-radius: 999px;
  border: 1px solid rgba(128,128,128,.28); font-size: .76rem;
}
.stButton > button, .stDownloadButton > button { border-radius: 12px; font-weight: 500; }
[data-testid="stChatInput"] textarea { font-size: 1rem; }
</style>
"""


def badge(agent):
    icon, name, _ = AGENT_INFO[agent]
    return f'<span class="badge badge-{agent}">{icon} {name}</span>'


def model_note(info):
    """Small grey text like '1.2s · openai/gpt-oss-120b' (+ fallback warning)."""
    note = f"{info['seconds']}s · {info['model']}"
    if info.get("fallback_used"):
        note += " · ⚠️ backup model used"
    return note


# ---------------------------------------------------------------
# Drawing events
# ---------------------------------------------------------------
def show_event(event, ui, key):
    """
    Draw one event from run_request().
    ui  : dict that remembers the current status boxes, so a later event
          (e.g. step_done) can update the box created by step_start.
    key : unique prefix for buttons in this message.
    """
    kind = event["type"]

    if kind == "planning":
        ui["plan"] = st.status("🧠 Orchestrator is planning your request…", expanded=False)

    elif kind == "plan":
        steps = event["steps"]
        box = ui["plan"]
        if steps:
            box.update(label=f"🧠 Plan ready — {len(steps)} step{'s' if len(steps) > 1 else ''}", state="complete")
            rows = "".join(
                f'<div class="plan-step"><div class="plan-num">{i}</div>{badge(s["agent"])}'
                f'<span class="plan-task">{html.escape(s["task"])}</span></div>'
                for i, s in enumerate(steps, start=1)
            )
            box.markdown(rows + f'<div class="meta">{html.escape(model_note(event))}</div>', unsafe_allow_html=True)
        else:
            box.update(label="🧠 Nothing I can do for this one", state="complete")
            if event.get("reply"):  # why not, and what to try instead
                box.markdown(html.escape(event["reply"]))
            box.markdown(f'<div class="meta">{html.escape(model_note(event))}</div>', unsafe_allow_html=True)

    elif kind == "step_start":
        icon, name, _ = AGENT_INFO[event["agent"]]
        ui["step"] = st.status(
            f"{icon} Step {event['number']}/{event['total']} · {name} is working…", state="running", expanded=True
        )
        ui["step"].caption(event["task"])

    elif kind == "step_done":
        show_step_result(event, ui["step"], key)

    elif kind == "step_failed":
        icon, name, _ = AGENT_INFO[event["agent"]]
        box = ui["step"]
        box.update(label=f"{icon} Step {event['number']} · {name} failed", state="error", expanded=True)
        box.error(event["message"])

    elif kind == "error":
        st.error(event["message"])

    elif kind == "done":
        if event.get("nothing_to_do") and event.get("reply"):
            st.info(event["reply"])
        elif event.get("nothing_to_do"):
            st.info(
                "I can **write** letters and emails, **open** apps and sites, read your Gmail, "
                "find files, get the news, do maths and send WhatsApp messages, but not that one. Try one of the examples in the sidebar!"
            )
        elif event["ok"]:
            st.success("All done! ✨")
        else:
            st.warning("I stopped because a step failed. Please try again.")


def show_step_result(event, box, key):
    """Fill a finished step's status box with the agent's result."""
    icon, name, _ = AGENT_INFO[event["agent"]]
    result = event["result"]
    number = event["number"]

    if event["agent"] == "writer":
        file_name = os.path.basename(result["file"])
        article = "an" if result["type"] == "email" else "a"
        box.update(label=f"{icon} Step {number} · {name} wrote {article} {result['type']}", state="complete", expanded=True)
        box.code(result["content"], language=None, wrap_lines=True)
        col1, col2 = box.columns(2)
        col1.download_button(
            "⬇️ Download", data=result["content"], file_name=file_name,
            mime="text/plain", key=f"{key}_dl_{number}", width="stretch", on_click="ignore",
        )
        col2.button(
            "📂 Open file", key=f"{key}_open_{number}", width="stretch",
            on_click=safe_open, args=(result["file"],),
        )
        box.markdown(f'<div class="meta">💾 {html.escape(file_name)} · {html.escape(model_note(result))}</div>',
                     unsafe_allow_html=True)

    else:  # system agent
        refused = result["action"]["action"] == "refuse"
        label = "refused" if refused else "done"
        box.update(label=f"{icon} Step {number} · {name} {label}", state="complete", expanded=True)
        if refused:
            box.warning("⛔ " + result["message"])
        else:
            box.success(result["message"])
        box.markdown(f'<div class="meta">{html.escape(model_note(result))}</div>', unsafe_allow_html=True)


def safe_open(path):
    """Open a file from outputs/ when the user clicks 'Open file'."""
    if os.path.isfile(path) and os.path.dirname(os.path.abspath(path)) == os.path.abspath(config.OUTPUT_DIR):
        try:
            open_file(path)
        except OSError:
            pass


def use_example(text):
    """Example button clicked: send it as if the user typed it (without the emoji)."""
    st.session_state.pending = text.split(" ", 1)[1]


# ---------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------
def draw_sidebar():
    with st.sidebar:
        st.markdown("### 🤖 Your agents")
        for agent, (icon, name, desc) in AGENT_INFO.items():
            model = config.AGENT_MODELS.get(agent, {}).get("model", "no AI model (plain Python)")
            st.markdown(
                f'<div class="agent-card"><span class="icon">{icon}</span><div>'
                f'<b>{name}</b><small>{desc}</small><code>{html.escape(model)}</code></div></div>',
                unsafe_allow_html=True,
            )

        st.markdown("### 🔓 What I can open")
        chips = "".join(f'<span class="chip">{html.escape(a)}</span>' for a in config.ALLOWED_APPS)
        chips += "".join(f'<span class="chip">🌐 {html.escape(s)}</span>' for s in config.ALLOWED_SITES)
        st.markdown(chips, unsafe_allow_html=True)

        st.markdown("### 💡 Try these")
        for i, example in enumerate(EXAMPLES):
            st.button(example, key=f"side_ex_{i}", on_click=use_example, args=(example,), width="stretch")

        st.divider()
        if st.button("🗑️ Clear chat", width="stretch"):
            st.session_state.messages = []
            st.session_state.memory = {}
            st.rerun()
        st.caption("🔒 Emails and WhatsApp messages are sent only when you ask to send them, and only to "
                   "addresses or saved contacts you name. Risky apps are blocked.")


# ---------------------------------------------------------------
# Main page
# ---------------------------------------------------------------
def main():
    st.markdown(CSS, unsafe_allow_html=True)
    if "messages" not in st.session_state:
        st.session_state.messages = []  # [{"role": "user", "text": ...} or {"role": "assistant", "events": [...]}]
    if "memory" not in st.session_state:
        st.session_state.memory = {}  # remembers the last answer, so follow-ups like "send this" work

    draw_sidebar()

    st.markdown(
        '<div class="hero"><div class="logo">🤖</div><h1>Aura</h1>'
        "<p>Your multi-agent helper — I plan, write, and open things for you.</p></div>",
        unsafe_allow_html=True,
    )

    # Read new input: typed in the chat box, or an example button
    prompt = st.chat_input("Ask me to write something or open an app…")
    if not prompt:
        prompt = st.session_state.pop("pending", None)

    messages = st.session_state.messages

    # Empty chat: show a friendly welcome with example buttons
    if not messages and not prompt:
        st.markdown(
            '<div class="welcome"><h3>👋 Hi! What can I do for you?</h3>'
            "<p>Ask in plain English. The <b>Orchestrator</b> splits your request into steps, "
            "the <b>Writer</b> drafts letters and emails, and the <b>System</b> agent opens apps and websites.</p></div>",
            unsafe_allow_html=True,
        )
        cols = st.columns(2)
        for i, example in enumerate(EXAMPLES):
            cols[i % 2].button(example, key=f"main_ex_{i}", on_click=use_example, args=(example,), width="stretch")

    # Redraw the chat history
    for index, message in enumerate(messages):
        if message["role"] == "user":
            with st.chat_message("user", avatar="🧑‍🎓"):
                st.markdown(message["text"])
        else:
            with st.chat_message("assistant", avatar="🤖"):
                ui = {}
                for event in message["events"]:
                    show_event(event, ui, key=f"m{index}")

    # Handle a new request: run the agents and draw events live.
    # This page can send email and open apps, so it only acts for a browser on this PC.
    if prompt and st.context.ip_address not in (None, "127.0.0.1", "::1", "localhost"):
        st.error("Aura only takes requests from this computer.")
        prompt = None
    if prompt:
        messages.append({"role": "user", "text": prompt})
        with st.chat_message("user", avatar="🧑‍🎓"):
            st.markdown(prompt)

        with st.chat_message("assistant", avatar="🤖"):
            ui, events = {}, []
            key = f"m{len(messages)}"  # index this assistant message will have
            for event in run_request(prompt, memory=st.session_state.memory):
                events.append(event)
                show_event(event, ui, key=key)
        messages.append({"role": "assistant", "events": events})


main()
