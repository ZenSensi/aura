"""
agents/tutor.py - The Tutor agent.

Solves and explains problems like a professor: maths (algebra, equations,
calculus, statistics, proofs, word problems), physics, chemistry, biology,
and programming in any language (write, debug, explain code).

It uses a reasoning model with reasoning_effort "high" (config.py), so it
thinks the problem through before answering, then shows the steps.

The answer is Markdown with LaTeX maths ($...$) and fenced code blocks;
the app draws it with marked + KaTeX (static/vendor). Like the Chat agent
it sees this chat's history, so "now do it in Java" or "why step 3?" work.

The answer is saved in state["last_answer"], so a later Writer / WhatsApp
step can use it ("send this solution to mahi").
"""

import re
import time

from llm import call_llm

SYSTEM_PROMPT = """You are Aura's Tutor: a brilliant, patient professor of mathematics, physics, chemistry,
biology and computer science. The user's name is {name}.

How to answer:
- Solve the problem completely and correctly. Think it through carefully before writing.
- Show the working as short numbered steps, each saying what you do and why, then give the result
  on its own line as "**Answer:** <the result>" (on that same line, with units for physics/chemistry).
- Check your result (substitute it back, check units, balance atoms, or test the code mentally)
  and fix any mistake before answering. Never guess; if the question is ambiguous, say which
  reading you solved.
- Maths: write formulas in LaTeX, inline as $...$ and on their own line as $$...$$.
  Never use \\( \\) or \\[ \\].
- Chemistry: balanced equations in plain LaTeX (no \\ce), e.g. $2H_2 + O_2 \\rightarrow 2H_2O$.
- Code: complete, runnable code in a fenced block with the language name (```python ...```),
  then a short explanation, and the expected output or complexity when helpful.
  If the user doesn't name a language, use Python.
- Concept questions (biology, theory): a clear explanation with key terms in bold and a short example.
- Use Markdown: short paragraphs, **bold** for key ideas, lists and small tables when they help.
  No long introductions; match the length to the problem.
- Use the earlier messages for follow-ups like "now in Java", "why step 3?" or "explain more simply"."""

MAX_TURNS = 6          # earlier turns sent to the model
MAX_TURN_CHARS = 3000  # each earlier message is cut to this length (solutions can be long)


def run_tutor(task, state):
    """Run the Tutor agent on the user's own words (state["request"]), using the chat history."""
    start = time.time()
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(name=state.get("user_name") or "not known")}]
    for turn in state.get("history", [])[-MAX_TURNS:]:
        messages.append({"role": "user", "content": turn["user"][:MAX_TURN_CHARS]})
        messages.append({"role": "assistant", "content": turn["assistant"][:MAX_TURN_CHARS]})
    messages.append({"role": "user", "content": state.get("request") or task})

    reply = call_llm("tutor", messages)
    answer = reply["text"].strip()
    if not answer:
        raise ValueError("I couldn't work that out. Please try again.")
    state["last_answer"] = answer  # for a later Writer / WhatsApp step

    return {
        "agent": "tutor", "model": reply["model"], "seconds": round(time.time() - start, 2),
        "fallback_used": reply["fallback_used"],
        "action": {"action": "tutor", "target": task},
        "message": first_line(answer),
        "answer": answer,
    }


SYMBOLS = {r"\rightarrow": "→", r"\to": "→", r"\times": "×", r"\cdot": "·", r"\pm": "±", r"\sqrt": "√",
           r"\pi": "π", r"\theta": "θ", r"\infty": "∞", r"\le": "≤", r"\ge": "≥", r"\neq": "≠", r"\approx": "≈"}


def first_line(answer):
    """A short line for the notification and the dock: the "Answer:" line if there is one, without LaTeX."""
    lines = [line.strip() for line in answer.splitlines()]
    start = next((i for i, l in enumerate(lines) if l.lower().lstrip("*# ").startswith("answer")), None)
    if start is None:
        line = next((l for l in lines if l and not l.startswith("```")), "Solved.")
    else:
        line, picked = lines[start], []
        if not re.sub(r"[*#\s]|answer:?", "", line, flags=re.I):  # "**Answer:**" alone: the result is below it
            for l in lines[start + 1:]:
                if l.startswith("```"):
                    picked = picked or [f"Here's the {l[3:].strip() or 'code'} code."]
                    break
                if not l:
                    if picked:
                        break
                    continue
                picked.append(l.lstrip("-* "))
                if len(picked) == 3:
                    break
            line = "Answer: " + "; ".join(picked)
    line = re.sub(r"\\(?:displaystyle|textstyle|left|right)(?![a-zA-Z])", "", line)
    line = re.sub(r"\\(?:text|mathrm|mathbf|operatorname)\{([^}]*)\}", r"\1", line)  # \text{ or } -> " or "
    line = re.sub(r"\\frac\{([^}]*)\}\{([^}]*)\}", r"(\1)/(\2)", line)
    for command, symbol in sorted(SYMBOLS.items(), key=lambda kv: -len(kv[0])):  # \to after \theta etc.
        line = re.sub(re.escape(command) + r"(?![a-zA-Z])", symbol, line)
    line = re.sub(r"\\[,;:! ]|\\quad|\\qquad|\\[()\[\]]", " ", line)  # spacing and \( \) delimiters
    line = re.sub(r"\\([a-zA-Z]+)", r"\1", line)                       # any other \command -> its name
    line = line.replace("**", "").replace("$", "").replace("{", "").replace("}", "")
    return " ".join(line.split())[:200]
