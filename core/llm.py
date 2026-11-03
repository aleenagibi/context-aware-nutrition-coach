"""Groq LLM layer: workout text -> validated JSON, and targets -> meal plan.
The LLM handles language; Python validates sessions and computes nutrition totals.

Two error types on purpose:
  LLMError    - the model answered but the output was unusable (bad JSON, silly portions). Retrying the prompt helps.
  LLMAPIError - the call itself failed (bad key, rate limit, network). Retrying the prompt does NOT help.
"""
import json
import os
import re
import time

from .foods import UNIT_SYNONYMS, normalise_unit
from .workout import CATEGORIES, INTENSITIES, make_session

DEFAULT_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
RETRY_DELAYS = (4, 8)  # seconds to wait after an HTTP 429 (free tier is rate-limited); the eval raises this


class LLMError(Exception):
    """The model's output could not be used safely."""


class LLMAPIError(Exception):
    """The Groq request itself failed."""


def make_client(api_key):
    if not api_key:
        return None
    try:
        from groq import Groq  # lazy import so the core and tests work without the SDK
    except ImportError as exc:
        raise LLMAPIError("Groq SDK is not installed. Run: pip install groq") from exc
    return Groq(api_key=api_key)


def _friendly(exc):
    code = getattr(exc, "status_code", None)
    if code in (401, 403):
        return f"Groq rejected the API key ({code}). Check GROQ_API_KEY."
    if code == 429:
        return "Groq rate limit reached. Wait a minute and try again."
    if code in (400, 404):
        return f"Groq rejected the request ({code}). The model name may be wrong; check GROQ_MODEL."
    return f"Groq request failed ({type(exc).__name__})."


def create_with_retry(client, **kwargs):
    """chat.completions.create with backoff on HTTP 429; any other failure becomes LLMAPIError."""
    for delay in (*RETRY_DELAYS, None):
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as exc:
            if getattr(exc, "status_code", None) == 429 and delay is not None:
                time.sleep(delay)
                continue
            raise LLMAPIError(_friendly(exc)) from exc


def _ask(client, model, system, user, max_tokens=1500, temperature=0):
    response = create_with_retry(client, model=model, max_tokens=max_tokens, temperature=temperature,
                                 messages=[{"role": "system", "content": system}, {"role": "user", "content": user}])
    try:
        text = response.choices[0].message.content
    except (AttributeError, IndexError, TypeError) as exc:
        raise LLMError("Groq returned an unexpected response structure") from exc
    if not isinstance(text, str) or not text.strip():
        raise LLMError("Groq returned an empty text response")
    return text


def _parse_json(text):
    text = re.sub(r"```(?:json)?", "", text, flags=re.I).strip()
    starts = [i for i in (text.find("["), text.find("{")) if i != -1]
    if not starts:
        raise LLMError("no JSON found in model output")
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[min(starts):])
    except json.JSONDecodeError as exc:
        raise LLMError(f"invalid JSON: {exc}") from exc
    return obj


EXTRACT_SYSTEM = f"""You convert workout descriptions into JSON. Output ONLY a JSON array, no prose.
Each item: {{"activity": str, "category": one of {CATEGORIES}, "duration_min": number, "intensity": one of {INTENSITIES}}}
Rules: convert hours to minutes. Skip items with no stated duration. Use "moderate" if intensity is unclear.
If the text describes no workout (rest day), return []."""


def extract_workouts(client, text, model=DEFAULT_MODEL, retries=2):
    prompt = text
    for _ in range(retries):
        try:
            data = _parse_json(_ask(client, model, EXTRACT_SYSTEM, prompt))
            if not isinstance(data, list):
                raise LLMError("expected a JSON array")
            return [make_session(item) for item in data]
        except (LLMError, ValueError, TypeError) as exc:
            prompt = f"{text}\n\nYour previous output was invalid ({exc}). Return only a valid JSON array."
    raise LLMError("could not get valid workout JSON from the model")


EXTRACT_MEAL_SYSTEM = """You extract the foods someone ate from a short text. Output ONLY a JSON array, no prose.
Each item: {"name": str, "quantity": number or null, "unit": str or null}
Rules:
- name: the food as people say it, in simple English or common Indian names (roti, dal, paneer bhurji, boiled egg). No quantity words.
- quantity: the number stated ("2 rotis" -> 2, "half a katori" -> 0.5), else null.
- unit: only if stated, one of: g, ml, cup, katori, bowl, glass, piece, slice, tbsp, tsp, handful, scoop, plate. Counts like "2 rotis" have unit null.
- Do NOT estimate grams or nutrition. Do NOT invent foods that are not mentioned. Return [] if no food is mentioned."""

_UNITS = set(UNIT_SYNONYMS.values())


def _meal_item(d):
    if not isinstance(d, dict) or not isinstance(d.get("name"), str) or not d["name"].strip() or len(d["name"]) > 60:
        raise ValueError("each item needs a short text name")
    qty = d.get("quantity")
    if qty is not None:
        qty = float(qty)
        if not 0 < qty <= 5000:
            raise ValueError("quantity out of range")
    unit = normalise_unit(d.get("unit"))
    return {"name": d["name"].strip().lower(), "quantity": qty, "unit": unit if unit in _UNITS else None}


def extract_meals(client, text, model=DEFAULT_MODEL, retries=2):
    """Free-text meal -> [{name, quantity, unit}]. The model never supplies nutrition values."""
    prompt = text
    for _ in range(retries):
        try:
            data = _parse_json(_ask(client, model, EXTRACT_MEAL_SYSTEM, prompt))
            if not isinstance(data, list):
                raise LLMError("expected a JSON array")
            return [_meal_item(d) for d in data]
        except (LLMError, ValueError, TypeError) as exc:
            prompt = f"{text}\n\nYour previous output was invalid ({exc}). Return only a valid JSON array."
    raise LLMError("could not get valid meal JSON from the model")
