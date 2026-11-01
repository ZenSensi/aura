"""
llm.py - One shared way to talk to any AI model.

Contains:
  call_model()     -> sends messages to ONE model on an OpenAI-compatible API
  call_llm()       -> calls an agent's main model, switches to its fallback on error
  extract_json()   -> pulls a JSON object out of the model's reply
  call_llm_json()  -> call_llm + extract_json, retries once if JSON is bad
"""

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import requests

import config


class LLMError(Exception):
    """Raised when an API call or JSON parsing fails."""


# Today's AI usage, for the notch's "AI usage" tab: {"day": "2026-11-02",
# "providers": {"groq": {"ok": 12, "failed": 1, "last_error": "...", "last": epoch}}}.
# In memory only (starts again with Aura or at midnight).
USAGE = {"day": "", "providers": {}}
on_fallback = None  # set by desktop.py: on_fallback(failed_name, next_name, error) when a backup takes over


PROVIDER_NAMES = {"groq": "Groq", "gemini": "Gemini", "openrouter": "OpenRouter"}


def provider_name(choice):
    """'Groq', 'Gemini', 'OpenRouter' or the CLI's name ('Claude Code')."""
    if choice.get("provider") == "cli":
        return config.CLI_TOOLS[choice["cli"]]["name"]
    return PROVIDER_NAMES.get(choice["provider"], choice["provider"])


def _count(choice, ok, error=""):
    day = time.strftime("%Y-%m-%d")
    if USAGE["day"] != day:
        USAGE.update(day=day, providers={})
    entry = USAGE["providers"].setdefault(provider_name(choice), {"ok": 0, "failed": 0, "last_error": "", "last": 0})
    entry["ok" if ok else "failed"] += 1
    entry["last"] = time.time()
    if not ok:
        entry["last_error"] = str(error)[:160]


def call_model(choice, messages):
    """
    Send chat messages to one model.

    choice: a dict from config.py, e.g. {"provider": "groq", "model": "openai/gpt-oss-20b"}
    Returns: {"text": reply text, "model": model name, "seconds": time taken}
    Raises LLMError if anything goes wrong.
    """
    if choice["provider"] == "cli":
        return call_cli(choice["cli"], messages)
    provider = config.PROVIDERS[choice["provider"]]

    # Use the entry's own key if it has one, otherwise the provider's key
    api_key = choice.get("api_key") or provider["api_key"]
    if not api_key:
        raise LLMError(f"Missing API key for '{choice['provider']}'. Add it in Settings.")

    # Every OpenAI-compatible API uses the same URL path and body format
    url = provider["base_url"] + "/chat/completions"
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    }
    body = {
        "model": choice["model"],
        "messages": messages,
        "temperature": 0.3,  # low = more predictable output
        **choice.get("options", {}),  # extra settings for this model only, e.g. reasoning_effort
    }

    start = time.time()
    try:
        response = requests.post(url, headers=headers, json=body, timeout=config.API_TIMEOUT_SECONDS)
    except requests.RequestException as e:
        raise LLMError(f"Network error: {e}")
    seconds = round(time.time() - start, 2)

    # 429 = rate limited, other 4xx/5xx = other errors
    if response.status_code != 200:
        short = " ".join(response.text.split())[:200]  # one line, max 200 chars
        raise LLMError(f"API error {response.status_code}: {short}")

    try:
        text = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):  # e.g. "choices": null
        raise LLMError("Unexpected response format from API.")
    if not text:
        raise LLMError("Model returned an empty reply.")

    return {"text": text, "model": choice["model"], "seconds": seconds}


def call_cli(name, messages):
    """Send chat messages to a coding-agent CLI (config.CLI_TOOLS) as one prompt; same result shape as call_model."""
    tool = config.CLI_TOOLS[name]
    path = shutil.which(tool["command"][0])
    if not path:
        raise LLMError(f"{tool['name']} isn't installed (no '{tool['command'][0]}' command). Pick another in Settings.")
    base = os.path.join(config.BASE_DIR, "cli_work")
    os.makedirs(base, exist_ok=True)
    work = tempfile.mkdtemp(dir=base)  # empty folder per call: no project to look at, and the pictures go here
    parts, images = [], []
    for m in messages:
        content = m["content"]
        if isinstance(content, list):  # vision message: text parts + data:image/...;base64 pictures
            text = []
            for part in content:
                if part.get("type") == "image_url":
                    name = f"image_{len(images) + 1}.jpg"
                    with open(os.path.join(work, name), "wb") as f:
                        f.write(base64.b64decode(part["image_url"]["url"].split(",", 1)[1]))
                    images.append(name)
                else:
                    text.append(part.get("text", ""))
            content = "\n".join(text)
        parts.append(f"[{m['role']}]\n{content}")
    prompt = "\n\n".join(parts)
    if images:
        prompt += f"\n\nThe pictures are the files {', '.join(images)} in the current folder: look at them."
    prompt += "\n\nReply as the assistant, with only the reply text. Don't edit files or run commands."
    command = [path, *tool["command"][1:]]
    if images and tool.get("image_flag"):
        command[-1:-1] = [tool["image_flag"] + os.path.join(work, n) for n in images]  # before codex's final "-"
    if not tool["stdin"]:
        command.append(prompt)
    start = time.time()
    try:
        done = subprocess.run(command, input=prompt if tool["stdin"] else None, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=work, timeout=config.CLI_TIMEOUT_SECONDS,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise LLMError(f"{tool['name']} took longer than {config.CLI_TIMEOUT_SECONDS} s.")
    except OSError as e:  # e.g. the prompt is too long for a Windows command line (agy)
        raise LLMError(f"Couldn't start {tool['name']}: {e}")
    finally:
        shutil.rmtree(work, ignore_errors=True)  # the user's pictures don't stay on disk
    text = re.sub(r"\x1b\[[0-9;]*m", "", done.stdout).strip()  # no terminal colours
    if done.returncode != 0 or not text:
        short = " ".join(re.sub(r"\x1b\[[0-9;]*m", "", done.stderr or done.stdout).split())[-200:]
        raise LLMError(f"{tool['name']} failed: {short or 'empty reply'}")
    return {"text": text, "model": tool["name"], "seconds": round(time.time() - start, 2)}


def call_llm(agent_name, messages):
    """
    Call the model assigned to `agent_name` in config.py.
    If it fails (error, rate limit, timeout), try the agent's fallback model,
    and if that fails too, try config.LAST_RESORT (openrouter/free).

    Returns the call_model() result plus "fallback_used": True/False.
    """
    settings = config.AGENT_MODELS[agent_name]
    # The models to try, in order: main -> fallback -> last resort
    chain = [settings]
    if settings.get("fallback"):
        chain.append(settings["fallback"])
    if (config.LAST_RESORT and config.PROVIDERS[config.LAST_RESORT["provider"]]["api_key"]
            and not settings.get("no_last_resort")):
        chain.append(config.LAST_RESORT)
    # The other engine takes over when the chosen one fails or hits its limit (same messages, so no lost context)
    cli = config.agent_cli(agent_name)
    cli_choice = {"provider": "cli", "cli": cli, "model": config.CLI_TOOLS[cli]["name"]}
    if config.AI_MODE == "cli":
        # Agents first, then only the API models whose key is saved (a missing key isn't an error here)
        chain = [cli_choice] + [c for c in chain if c.get("api_key") or config.PROVIDERS[c["provider"]]["api_key"]]
    elif shutil.which(config.CLI_TOOLS[cli]["command"][0]):  # API first; an agent only if it's installed
        chain.append(cli_choice)

    for position, choice in enumerate(chain):
        try:
            result = call_model(choice, messages)
            _count(choice, True)
            result["fallback_used"] = position > 0  # True if any backup answered
            return result
        except LLMError as e:
            _count(choice, False, e)
            if position == len(chain) - 1:
                raise  # no backups left
            if on_fallback:
                try:
                    on_fallback(provider_name(choice), provider_name(chain[position + 1]), str(e))
                except Exception:
                    pass  # a display problem must not stop the agents
            print(f"  [llm] {agent_name}: {choice['model']} failed ({e}). "
                  f"Trying backup {chain[position + 1]['model']}...")


def extract_json(text):
    """
    Turn a model reply into a Python dict.

    Models often wrap JSON in ```json ... ``` fences or add extra words,
    so we: 1) remove the fences, 2) keep only the part from the first
    '{' to the last '}', 3) parse it with json.loads.
    Raises LLMError if no valid JSON object is found.
    """
    cleaned = text.replace("```json", "").replace("```", "").strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        raise LLMError("No JSON object found in reply.")

    try:
        result = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"Invalid JSON: {e}")

    if not isinstance(result, dict):
        raise LLMError("JSON reply is not an object.")
    return result


def call_llm_json(agent_name, system_prompt, user_message):
    """
    Ask a model for a JSON answer.
    If the reply is not valid JSON, ask again once (config.JSON_RETRIES)
    and tell the model what went wrong.

    Returns: {"data": parsed dict, "model": ..., "seconds": total time, "fallback_used": ...}
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    total_seconds = 0

    for attempt in range(1 + config.JSON_RETRIES):
        reply = call_llm(agent_name, messages)
        total_seconds += reply["seconds"]
        try:
            data = extract_json(reply["text"])
            return {
                "data": data,
                "model": reply["model"],
                "seconds": round(total_seconds, 2),
                "fallback_used": reply["fallback_used"],
            }
        except LLMError as e:
            print(f"  [llm] Bad JSON on attempt {attempt + 1}: {e}")
            # Show the model its bad reply and ask it to fix it
            messages.append({"role": "assistant", "content": reply["text"]})
            messages.append({"role": "user", "content": "Your reply was not valid JSON. Reply with ONLY the JSON object, nothing else."})

    raise LLMError("Model did not return valid JSON after retrying.")
