"""
llm.py - One shared way to talk to any AI model.

Contains:
  call_model()     -> sends messages to ONE model on an OpenAI-compatible API
  call_llm()       -> calls an agent's main model, switches to its fallback on error
  extract_json()   -> pulls a JSON object out of the model's reply
  call_llm_json()  -> call_llm + extract_json, retries once if JSON is bad
"""

import json
import time
import requests

import config


class LLMError(Exception):
    """Raised when an API call or JSON parsing fails."""


def call_model(choice, messages):
    """
    Send chat messages to one model.

    choice: a dict from config.py, e.g. {"provider": "groq", "model": "openai/gpt-oss-20b"}
    Returns: {"text": reply text, "model": model name, "seconds": time taken}
    Raises LLMError if anything goes wrong.
    """
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

    for position, choice in enumerate(chain):
        try:
            result = call_model(choice, messages)
            result["fallback_used"] = position > 0  # True if any backup answered
            return result
        except LLMError as e:
            if position == len(chain) - 1:
                raise  # no backups left
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
