"""
agents/chat.py - The Chat agent.

Talks with the user: greetings, small talk, jokes, advice, explaining ideas,
brainstorming, and follow-up questions about earlier answers.

It sees the conversation so far (state["history"]: the last few turns of
THIS chat), so "explain that more simply" or "and in Python?" make sense.
New chat (the button in the app) starts with an empty history.

Facts that change (news, prices, weather, results) are NOT for this agent:
the Orchestrator sends those to the Web agent.

The answer is saved in state["last_answer"], so a later Writer / WhatsApp
step can use it ("send that joke to mahi").
"""

import time
from datetime import date

from llm import call_llm

SYSTEM_PROMPT = """You are Aura, a friendly AI assistant in a Windows desktop app, talking with the user.
Today is {today}. The user's name is {name}.

- Answer like a helpful friend: warm, clear and to the point. Use complete sentences.
- Keep it short (1-4 sentences) for greetings and small talk; longer only when the user asks
  to explain, teach, plan or brainstorm something.
- Plain text only: no markdown, no ** or #. For lists use lines starting with "- " or "1. ".
- Use the earlier messages to understand follow-ups like "that", "it", "more simply".
- If asked what you can do: write letters and emails (and send them via Gmail), open apps and websites,
  read and summarise Gmail, find files on this PC, give the news and stock charts, do exact maths,
  solve maths, physics, chemistry, biology and coding problems step by step like a professor,
  search the web with sources, read attached files and pictures, and send WhatsApp messages.
- You cannot see the screen or do things on the PC yourself in this chat; if the user wants an action,
  tell them to just ask for it (e.g. "Open Spotify").
- Don't invent recent facts (news, prices, scores, weather). Say you can look it up if they ask
  you to search the web."""

MAX_TURNS = 8          # earlier turns sent to the model
MAX_TURN_CHARS = 1500  # each earlier message is cut to this length


def run_chat(task, state):
    """Run the Chat agent. Answers the user's own words (state["request"]), using the chat history."""
    start = time.time()
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(
        today=date.today().strftime("%A, %d %B %Y"), name=state.get("user_name") or "not known")}]
    for turn in state.get("history", [])[-MAX_TURNS:]:
        messages.append({"role": "user", "content": turn["user"][:MAX_TURN_CHARS]})
        messages.append({"role": "assistant", "content": turn["assistant"][:MAX_TURN_CHARS]})
    messages.append({"role": "user", "content": state.get("request") or task})

    reply = call_llm("chat", messages)
    answer = reply["text"].strip().replace("**", "")
    if not answer:
        raise ValueError("I couldn't think of a reply. Please try again.")
    state["last_answer"] = answer  # for a later Writer / WhatsApp step

    return {
        "agent": "chat", "model": reply["model"], "seconds": round(time.time() - start, 2),
        "fallback_used": reply["fallback_used"],
        "action": {"action": "chat", "target": task},
        "message": answer,
        "answer": answer,
    }
