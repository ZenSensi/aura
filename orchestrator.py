"""
orchestrator.py - The Orchestrator agent and the request runner.

make_plan()    -> asks the model to split a request into steps (JSON plan)
run_request()  -> runs every step with the right agent, sharing one state dict

run_request() is a GENERATOR: it "yields" an event after each stage
(planning, plan ready, step started, step finished...). Both the terminal
(main.py) and the web UI (app.py) loop over these events to show progress
live. That way the agent logic is written once and used by both screens.
"""

import json
import os
import time
import uuid

import automations
import config
from llm import call_llm_json, LLMError
from logger import log_step
from agents.writer import run_writer
from agents.system import run_system
from agents.mailer import run_mailer
from agents.inbox import run_inbox
from agents.files import run_files
from agents.news import run_news
from agents.calc import run_calc
from agents.whatsapp import run_whatsapp
from agents.web import run_web
from agents.reader import run_reader
from agents.chat import run_chat
from agents.tutor import run_tutor
from agents.automate import run_automate
from agents.organiser import run_organiser
from agents.music import run_music
from agents.notion import run_notion

MAX_HISTORY = 12  # turns of the conversation kept per chat

# Which function runs each agent
AGENTS = {
    "writer": run_writer,
    "system": run_system,
    "mailer": run_mailer,
    "inbox": run_inbox,
    "files": run_files,
    "news": run_news,
    "calc": run_calc,
    "whatsapp": run_whatsapp,
    "web": run_web,
    "reader": run_reader,
    "chat": run_chat,
    "tutor": run_tutor,
    "automate": run_automate,
    "organiser": run_organiser,
    "music": run_music,
    "notion": run_notion,
}

# Agents the user switched off in the sidebar (the Orchestrator can't be switched off)
OFF_FILE = os.path.join(config.BASE_DIR, "agents_off.json")


def agents_off():
    """Names of the switched-off agents, e.g. {"whatsapp"}."""
    try:
        with open(OFF_FILE, encoding="utf-8") as f:
            return {name for name in json.load(f) if name in AGENTS}
    except (OSError, ValueError, TypeError):
        return set()


def set_agent_on(name, on):
    """The light in the sidebar's Agents list was clicked. Returns the new set of switched-off agents."""
    off = agents_off()
    if name in AGENTS:
        off = off - {name} if on else off | {name}
        with open(OFF_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(off), f)
    return off


def blocked_reply(off):
    """What Aura says when a plan needs a switched-off agent."""
    names = " and ".join({"whatsapp": "WhatsApp"}.get(a, a.title()) for a in off)
    many = len(off) > 1
    return (f"That needs the {names} agent{'s' if many else ''}, which {'are' if many else 'is'} switched off. "
            "Click its light in the Agents list to turn it back on.")

SYSTEM_PROMPT = f"""You are the Orchestrator of a desktop assistant.
Split the user's request into steps and give each step to one agent.

Agents:
- "writer": writes letters, emails and short messages, and saves them as a file.
- "system": opens any app installed on this computer (e.g. {", ".join(config.ALLOWED_APPS)}, VS Code,
            WhatsApp, Spotify, Chrome, Word), an allowed website ({", ".join(config.ALLOWED_SITES)}),
            or the last file the writer saved.
- "mailer": sends the last email the writer wrote to the email address(es) the user typed,
            or replies to the email the inbox agent found.
- "inbox": reads and searches the user's Gmail inbox and summarises emails (read-only).
- "news": today's news briefing (tech, finance, India, world, science, health, sports, entertainment,
          or news about a named subject) and live stock market prices with charts (indices or named stocks).
- "files": searches the user's own folders (Desktop, Documents, Downloads, Pictures, Music, Videos)
           for files by name, type and date, and lists them (read-only).
- "calc": works out arithmetic with plain numbers only, exactly (+ - * / % powers, brackets). Its task is
          ONLY the expression, e.g. "6789 + 12344" or "(25 * 48) / 3".
- "tutor": a professor that SOLVES and explains problems step by step: maths (equations, algebra, calculus,
           trigonometry, matrices, statistics, probability, proofs, word problems), physics, chemistry,
           biology, and programming in any language (write, debug, explain or convert code).
           Its task is the user's message.
- "whatsapp": sends a message to ONE saved contact on WhatsApp: either the user's exact words
              (in double quotes in its task), or the last message the writer wrote, or the last answer.
- "web": searches the web and answers from the results, with sources. For questions about facts,
         people, places, products, prices, weather, sports results, how-tos, "what is X"...
- "reader": reads the file the user ATTACHED (PDF, picture, Word or text file) and answers about it:
            summarise, explain, describe, extract details. Only when the message says a file is attached.
- "chat": talks with the user: greetings, small talk, thanks, jokes, opinions, advice, motivation,
          explaining a general topic or idea, brainstorming, planning, "what can you do",
          and follow-up questions about earlier answers in the conversation. Its task is the user's message.
- "automate": saves things to do LATER or AGAIN: reminders ("remind me at 5 PM to call papa", "in 20 minutes",
              "add buy milk to my tasks / to-do list"),
              routines that run a request at a time or on a schedule ("every day at 8 AM give me the news",
              "every Monday summarise my unread emails", "at 9 PM send "good night" to mummy"), and workflows
              (a request saved under a name: "save 'open VS Code and Spotify' as study mode").
              It also lists, pauses, resumes and deletes them. Its task is the user's whole message.
- "organiser": tidies the Downloads, Desktop or Documents folder by moving loose files into folders by type
               (PDFs, Images, Installers...), previews a tidy-up, or undoes the last one. It never deletes.
- "music": PLAYS a song on Spotify or YouTube Music (it finds the song and starts it), and pauses, resumes
           or skips the music. Its task is the user's words, e.g. "play kesariya on spotify".
- "notion": the user's Notion: searches pages, reads a named page and answers about it (summarise, list
            its tasks...), or saves a note / the last answer into a named page (or a new sub-page).
            Its task is the user's words about Notion, e.g. "summarise my Meeting notes page".

Rules:
- Keep the steps in the same order as the user asked.
- If only PART of a request can be done, plan the parts that can be done.
- At most {config.MAX_STEPS} steps.
- Each "task" is a short, clear instruction for that agent, e.g. "write a leave letter for 2 days" or "open gmail" or "open the last file".
- If the user asks to write something AND open it, use a writer step then a system step "open the last file".
- If the user gives the EXACT words to write (e.g. "write hello on notepad", "type good morning in notepad",
  "write 'see you at 5' in a file"), the writer task is those words copied exactly, in double quotes:
  'write exactly "hello"'. Do not change, fix or add to the words. Then the system step "open the last file"
  if they said notepad or asked to open it.
- If the user asks to SEND an email AND gives the recipient's email address: a writer step whose task
  ends with "(this email will be sent)", then a mailer step "send the last email to <address>".
- If the user asks to send but gives NO email address, only plan the writer step (we cannot send it).
- If the user gives the EXACT words to send on WhatsApp (e.g. "send hi to mahi", "whatsapp papa i'll be late"),
  plan ONLY a whatsapp step with those words copied exactly, in double quotes: 'send "hi" to mahi'.
  Do not change, fix or add to the words.
- If the user asks you to WRITE/compose a WhatsApp message (e.g. "wish mahi happy birthday", "write a sorry
  message to papa"): a writer step "write a short WhatsApp message to <name> saying ... (this will be sent)",
  then a whatsapp step "send the last message to <name>".
  Do NOT add a system step to open WhatsApp (the whatsapp agent opens it).
- To send a calc ANSWER on WhatsApp: after the calc step (and after the writer note, if the user also wants
  it in Notepad), a whatsapp step "send the answer to <name>".
- If the user asks to send something on WhatsApp but names NO person, STILL plan the whatsapp step
  (e.g. "send the answer") - the whatsapp agent will ask who to send it to. Never drop it.
- The message may start with the conversation so far. That is ONLY for references like
  "this answer", "it", "that message" in the new request: e.g. "send this answer to mummy on whatsapp"
  = ONE whatsapp step "send the last answer to mummy"; "open it" = a system step "open the last file".
  Plan ONLY what the NEW request asks; never repeat the previous request's steps.
- Anything to do LATER or AGAIN (a time in the future, "every", "daily", "each morning", "tomorrow",
  "in 10 minutes", "remind me", "schedule", "save this as", routines, reminders, workflows, adding a task or
  to-do (also to Google Tasks / Calendar / the phone)) is ONE automate step
  whose task is the user's whole message, copied exactly. Never ALSO do the task now.
- Organising, tidying, cleaning up or sorting the Downloads / Desktop / Documents folder (or undoing it) is ONE
  organiser step, e.g. "organise my downloads folder". Deleting files is not allowed: return {{"steps": []}}.
- "Play <song>" (on Spotify / YouTube Music / YouTube, or no platform named) is ONE music step whose task
  keeps the song, the artist and the platform exactly, e.g. "play shape of you by ed sheeran on youtube music". Pause / resume / next /
  previous song are a music step too. Only "open Spotify" (no song) is a system step.
- Anything that names Notion (search, read, summarise, add, save, note to Notion) is a notion step.
  "Search X and save it to Notion" = a web step, then a notion step "save the last answer to <page>".
  Only "open Notion" (nothing to read or save) is a system step.
- To read, check, search or summarise emails, use ONE inbox step, e.g. "summarise my 5 latest unread emails".
- To REPLY to an email: an inbox step that finds it (e.g. "find the latest email from monika"),
  then a writer step "write a reply saying ... (this email will be sent)", then a mailer step "send the reply".
- For news, headlines, "what's happening", stock market, share prices or indices, use ONE news step.
  Single fact questions (who won X, weather, what is X) are NOT news: use ONE web step.
- For arithmetic with plain numbers only ("calculate", "solve 25 * 48", "what is 12% ..."), use ONE calc step
  per calculation.
- Anything else to solve, derive, prove or code (equations with x or other letters, functions, formulas,
  units, word problems, physics/chemistry/biology questions or numericals, "write a program", "fix this code",
  "what is the time complexity") is ONE tutor step. Follow-ups about an earlier solution or code
  ("why step 2?", "now in Java", "explain more simply") are a tutor step too.
  Other agents can't type into apps: "calculate X in the calculator" = a system step "open calculator"
  (only if the user asked to open it) plus a calc step "X".
- To write/put/save the answer in Notepad or a file: AFTER the calc step, a writer step
  "write a short note with the answer" then a system step "open the last file" (it opens in Notepad).
- To find, search or look for FILES on the computer (e.g. "find my resume", "PDFs from last week"), use ONE files step.
  To find a file AND open it: the files step, then a system step with EXACTLY the task "open the last file".
- Deleting, moving or forwarding emails is not possible: return {{"steps": []}}.
- Opening an app is always a system step, even if you don't know whether it is installed
  (the system agent checks). Opening terminals, PowerShell or cmd, or running commands, is NOT allowed: return {{"steps": []}}.
- Questions that need information (facts, weather, prices, "who/what/when/how...") use ONE web step whose
  task is the search, e.g. "search the web for the weather in Delhi today".
- If the message says "Attached file: ...", requests about it ("summarise this", "what does this image show",
  or no clear request) use a reader step, e.g. "summarise the attached file". To send or save that answer,
  add the writer / mailer / whatsapp steps after it, like for any other answer.
- If the message says "Web search is ON", the plan MUST include a web step that searches for what the user
  typed, written out in full, e.g. "search the web for best laptops under 60000" (never "the user's request").
- If the request cannot be done by these agents, return {{"steps": []}}.
- Conversation (greetings, small talk, jokes, advice, "explain X", "help me plan/understand...", questions
  about earlier answers like "explain that more simply") is ONE chat step. Use web instead when the answer
  needs fresh or looked-up facts (news, prices, weather, scores, a specific person/product/place details).
  Chat is also the fallback: if a message is not a task for another agent and not forbidden, use ONE chat step.
- Whenever "steps" is empty (the request is not allowed or not possible), ALSO add "reply": 1-2 short,
  friendly sentences saying what you can't do and why, and suggesting something close that you can do.

Reply with ONLY this JSON, nothing else:
{{"steps": [{{"agent": "writer" | "system" | "mailer" | "inbox" | "files" | "news" | "calc" | "whatsapp" | "web" | "reader" | "chat" | "tutor" | "automate" | "organiser" | "music" | "notion", "task": "..."}}]}}
(with "reply": "..." added only when "steps" is empty)"""


def validate_plan(data):
    """Keep only well-formed steps, and at most MAX_STEPS of them."""
    steps = data.get("steps", [])
    if not isinstance(steps, list):
        raise LLMError("Plan 'steps' is not a list.")

    clean = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        agent = str(step.get("agent", "")).strip().lower()
        task = str(step.get("task", "")).strip()
        if agent in AGENTS and task:
            clean.append({"agent": agent, "task": task})

    return clean[:config.MAX_STEPS]  # hard limit


def describe_previous(memory):
    """The last few turns of this chat, so the Orchestrator understands "this answer" / "it" / "that"."""
    turns = (memory or {}).get("history", [])[-3:]
    if not turns:
        return ""
    lines = [f"User: {t['user'][:300]}\nAura: {t['assistant'][:300]}" for t in turns]
    return "Conversation so far:\n" + "\n".join(lines) + "\n\nNew request: "


def turn_summary(state, results):
    """What Aura 'said' in one request, for the chat history: an answer, the written text, or what was done."""
    if state["last_answer"]:
        return state["last_answer"]
    if state["last_file"] and os.path.isfile(state["last_file"]):
        with open(state["last_file"], encoding="utf-8") as f:
            return "Wrote this:\n" + f.read().strip()[:800]
    return " ".join(r.get("message") or "" for r in results).strip() or "Done."


def make_plan(user_text, memory=None, attachment=None, web=False, only=None):
    """Ask the Orchestrator model for a plan. Returns (steps, reply info).
    only: the agent the user picked in the sidebar; the plan then uses just that agent."""
    notes = ""
    if attachment:
        notes += f"Attached file: {os.path.basename(attachment)}\n"
    if web:
        notes += "Web search is ON\n"
    if only:
        notes += f'The user picked the "{only}" agent: plan ONLY "{only}" steps.\n'
    reply = call_llm_json("orchestrator", SYSTEM_PROMPT, notes + describe_previous(memory) + user_text)
    steps = validate_plan(reply["data"])

    # The + menu choices are promises to the user, so make sure the plan keeps them
    agents = [step["agent"] for step in steps]
    if web and "web" not in agents:
        steps.insert(0, {"agent": "web", "task": f"search the web for {user_text}"})
    if attachment and not steps:
        steps = [{"agent": "reader", "task": user_text}]
    if only:  # the model still words the task (e.g. Calc's expression), but only that agent runs
        steps = [step for step in steps if step["agent"] == only] or [{"agent": only, "task": user_text}]
    return steps[:config.MAX_STEPS], reply


def run_request(user_text, dry_run=False, memory=None, attachment=None, web=False, automated=False, agent=None):
    """
    Run one user request from start to finish, yielding events.
    Every step (planning + each agent) is saved to logs/run_log.jsonl.

    memory: a dict the caller keeps between requests (the desktop window keeps one).
    It remembers the previous request's answer and file, so a follow-up like
    "send this answer to mummy on whatsapp" knows what "this answer" is.

    dry_run=True: the System agent decides what to open but does not
    actually open it (used by the test script).

    attachment: path of the file picked with the + button (the Reader agent reads it).
    Follow-ups ("what does page 3 say?") keep using the previous request's file.
    web=True: the user turned on "Web search" in the + menu, so the plan always searches.
    automated=True: a routine started this request (automations.py), so it may not
    save new automations (a routine can never multiply itself).
    agent: the agent the user clicked in the sidebar ("talk to Music"): only that agent runs.
    A plan that needs a switched-off agent (agents_off) runs nothing and says why.

    Typing a saved workflow's name ("study mode", "run study mode") runs its saved request.

    Event types:
      planning   -> orchestrator started
      plan       -> plan is ready (steps list)
      step_start -> an agent started a step
      step_done  -> an agent finished a step (result)
      step_failed-> an agent crashed (error message)
      error      -> planning itself failed
      done       -> everything finished
    """
    # Fresh shared memory for THIS request. Agents read/write it,
    # e.g. Writer sets state["last_file"], System opens it.
    # The Mailer only sends to addresses found in "request" (what the user typed).
    # "reply_to" is the email the Inbox agent found, so Writer + Mailer can reply to it.
    # "last_answer" is the Calc agent's result, so the Writer can put it in a note.
    # "previous" is the last request's answer/file, only used for follow-ups like "send this answer".
    # "attachment" is the file the Reader agent reads (this request's, or the previous one's).
    attachment = attachment or (memory or {}).get("attachment")
    workflow = automations.find_workflow(user_text)
    request = workflow["task"] if workflow else user_text  # what the agents work on
    state = {"last_file": None, "last_type": None, "request": request, "reply_to": None,
             "last_answer": None, "dry_run": dry_run, "previous": dict(memory or {}),
             "attachment": attachment, "history": list((memory or {}).get("history", [])),
             "user_name": config.USER_NAME}
    request_id = uuid.uuid4().hex[:8]  # groups all log lines of one request

    # ---- 1. Plan ----
    yield {"type": "planning"}
    start = time.time()
    try:
        steps, reply = make_plan(request, memory, attachment, web, agent if agent in AGENTS else None)
        if automated:
            steps = [step for step in steps if step["agent"] != "automate"]
    except LLMError as e:
        log_step(request_id, "orchestrator", config.AGENT_MODELS["orchestrator"]["model"],
                 user_text, "", time.time() - start, success=False, error=str(e))
        yield {"type": "error", "message": f"Could not make a plan: {e}"}
        yield {"type": "done", "ok": False}
        return

    log_step(request_id, "orchestrator", reply["model"], user_text, {"steps": steps},
             time.time() - start, success=True, fallback_used=reply["fallback_used"])
    # No steps: the Orchestrator's own short answer (greeting, small talk, why it can't help)
    chat = "" if steps else str(reply["data"].get("reply") or "").strip()[:600]
    off = sorted({step["agent"] for step in steps} & agents_off())
    if off:  # never run half a plan (e.g. Writer without the Mailer it was writing for)
        steps, chat = [], blocked_reply(off)
    yield {
        "type": "plan",
        "steps": steps,
        "reply": chat,
        "model": reply["model"],
        "seconds": reply["seconds"],
        "fallback_used": reply["fallback_used"],
        "workflow": workflow["name"] if workflow else None,
    }

    if not steps:
        if memory is not None and chat:
            memory["history"] = (memory.get("history", []) + [{"user": user_text, "assistant": chat}])[-MAX_HISTORY:]
        yield {"type": "done", "ok": True, "nothing_to_do": True, "reply": chat}
        return

    # ---- 2. Run each step in order ----
    results = []
    for number, step in enumerate(steps, start=1):
        yield {"type": "step_start", "number": number, "total": len(steps), **step}
        start = time.time()
        try:
            result = AGENTS[step["agent"]](step["task"], state)
        except (LLMError, ValueError, OSError) as e:
            model = config.AGENT_MODELS.get(step["agent"], {}).get("model", "none")  # mailer has no model
            log_step(request_id, step["agent"], model,
                     step["task"], "", time.time() - start, success=False, error=str(e))
            yield {"type": "step_failed", "number": number, "total": len(steps), **step, "message": str(e)}
            yield {"type": "done", "ok": False}
            return  # stop: later steps may depend on this one

        # What to save as "output" for each agent
        if step["agent"] == "writer":
            output = {"file": result["file"], "content": result["content"]}
        else:
            output = {"action": result["action"], "message": result["message"]}
        log_step(request_id, step["agent"], result["model"], step["task"], output,
                 time.time() - start, success=True, fallback_used=result["fallback_used"])

        results.append(result)
        yield {"type": "step_done", "number": number, "total": len(steps), **step, "result": result}

    # Remember this request's answer and file for the next follow-up
    if memory is not None:
        # The conversation of this chat (New chat clears it), for the Chat agent and follow-ups
        history = (memory.get("history", []) + [{"user": user_text, "assistant": turn_summary(state, results)}])
        memory.clear()
        # The attached file is kept only while the user keeps asking about it
        read_file = any(step["agent"] == "reader" for step in steps)
        memory.update(request=user_text, last_answer=state["last_answer"], last_file=state["last_file"],
                      attachment=attachment if read_file else None, history=history[-MAX_HISTORY:])
    yield {"type": "done", "ok": True, "nothing_to_do": False}
