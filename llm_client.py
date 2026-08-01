"""Provider-agnostic LLM client with Gemini as the default backend.

Exposes generate_json(prompt, schema) so extract.py never hardcodes a provider.
To fail over to Groq or OpenRouter later, change LLM_PROVIDER / model settings
in .env — extraction logic stays untouched.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (env-driven so providers are swappable)
# ---------------------------------------------------------------------------

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
# Reserved for future failover — not required for Gemini path
OPENAI_COMPAT_BASE_URL = os.getenv("OPENAI_COMPAT_BASE_URL", "")
OPENAI_COMPAT_API_KEY = os.getenv("OPENAI_COMPAT_API_KEY", "")
OPENAI_COMPAT_MODEL = os.getenv("OPENAI_COMPAT_MODEL", "")

MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
RETRY_BACKOFF_SECONDS = float(os.getenv("LLM_RETRY_BACKOFF", "1.5"))


class LLMError(Exception):
    """Raised when the LLM call fails after retries."""


class LLMConfigError(LLMError):
    """Raised when required API credentials are missing."""


def _strip_json_fences(text: str) -> str:
    """Remove markdown code fences if the model wraps JSON anyway."""
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    return text


def _parse_json_strict(text: str) -> dict[str, Any]:
    """Parse JSON, raising ValueError on failure."""
    cleaned = _strip_json_fences(text)
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        # Wrap bare lists into a dict for schema consistency
        return {"items": data}
    return data


def _schema_hint(schema: dict[str, Any]) -> str:
    """Produce a compact schema reminder for the prompt."""
    return json.dumps(schema, indent=2)


def _build_prompt(prompt: str, schema: dict[str, Any]) -> str:
    """Append strict JSON instructions to the user prompt."""
    return (
        f"{prompt}\n\n"
        "Respond with ONLY valid JSON matching this schema. "
        "No markdown fences, no commentary, no trailing text.\n\n"
        f"JSON Schema:\n{_schema_hint(schema)}"
    )


# ---------------------------------------------------------------------------
# Gemini backend
# ---------------------------------------------------------------------------


def _generate_json_gemini(
    prompt: str,
    schema: dict[str, Any],
    model: Optional[str] = None,
) -> dict[str, Any]:
    """Call Google Gemini with JSON response mode."""
    if not GEMINI_API_KEY:
        raise LLMConfigError(
            "GEMINI_API_KEY is not set. Add it to a .env file "
            "(get a free key at https://aistudio.google.com/apikey)."
        )

    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)
    model_name = model or GEMINI_MODEL
    full_prompt = _build_prompt(prompt, schema)

    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # Prefer response_schema when the SDK supports it; fall back to
            # application/json MIME type + prompt schema enforcement.
            config_kwargs: dict[str, Any] = {
                "temperature": 0.1,
                "response_mime_type": "application/json",
            }
            try:
                config_kwargs["response_schema"] = schema
            except Exception:  # noqa: BLE001
                pass

            response = client.models.generate_content(
                model=model_name,
                contents=full_prompt,
                config=types.GenerateContentConfig(**config_kwargs),
            )
            raw = (response.text or "").strip()
            if not raw:
                raise ValueError("Empty response from Gemini")
            return _parse_json_strict(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            logger.warning(
                "Gemini JSON parse failed (attempt %s/%s): %s",
                attempt,
                MAX_RETRIES,
                exc,
            )
            # On malformed JSON, retry with a corrective nudge
            full_prompt = (
                _build_prompt(prompt, schema)
                + f"\n\nPrevious response was invalid JSON ({exc}). "
                "Return corrected JSON only."
            )
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            logger.warning(
                "Gemini call failed (attempt %s/%s): %s",
                attempt,
                MAX_RETRIES,
                exc,
            )
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    raise LLMError(f"Gemini generate_json failed after {MAX_RETRIES} attempts: {last_error}")


# ---------------------------------------------------------------------------
# OpenAI-compatible backend (Groq / OpenRouter failover stub)
# ---------------------------------------------------------------------------


def _generate_json_openai_compat(
    prompt: str,
    schema: dict[str, Any],
    model: Optional[str] = None,
) -> dict[str, Any]:
    """Call an OpenAI-compatible chat completions endpoint (Groq/OpenRouter)."""
    if not OPENAI_COMPAT_API_KEY or not OPENAI_COMPAT_BASE_URL:
        raise LLMConfigError(
            "OPENAI_COMPAT_API_KEY and OPENAI_COMPAT_BASE_URL must be set "
            "when LLM_PROVIDER is groq/openrouter/openai_compat."
        )

    import urllib.error
    import urllib.request

    model_name = model or OPENAI_COMPAT_MODEL or "llama-3.3-70b-versatile"
    full_prompt = _build_prompt(prompt, schema)
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "system",
                "content": "You are a careful information extraction engine. Output valid JSON only.",
            },
            {"role": "user", "content": full_prompt},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    body = json.dumps(payload).encode("utf-8")
    url = OPENAI_COMPAT_BASE_URL.rstrip("/") + "/chat/completions"

    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Authorization": f"Bearer {OPENAI_COMPAT_API_KEY}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            raw = data["choices"][0]["message"]["content"]
            return _parse_json_strict(raw)
        except (json.JSONDecodeError, ValueError, KeyError, urllib.error.URLError) as exc:
            last_error = exc
            logger.warning(
                "OpenAI-compat JSON call failed (attempt %s/%s): %s",
                attempt,
                MAX_RETRIES,
                exc,
            )
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    raise LLMError(
        f"OpenAI-compat generate_json failed after {MAX_RETRIES} attempts: {last_error}"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_json(
    prompt: str,
    schema: dict[str, Any],
    model: Optional[str] = None,
) -> dict[str, Any]:
    """Generate a JSON object matching *schema* from *prompt*.

    Routes to the configured provider. Retries on malformed JSON.
    """
    provider = LLM_PROVIDER
    if provider in {"gemini", "google"}:
        return _generate_json_gemini(prompt, schema, model=model)
    if provider in {"groq", "openrouter", "openai_compat", "openai"}:
        return _generate_json_openai_compat(prompt, schema, model=model)
    raise LLMConfigError(
        f"Unknown LLM_PROVIDER '{provider}'. Use gemini, groq, openrouter, or openai_compat."
    )


def is_configured() -> bool:
    """Return True if the active provider has credentials set."""
    if LLM_PROVIDER in {"gemini", "google"}:
        return bool(GEMINI_API_KEY)
    return bool(OPENAI_COMPAT_API_KEY and OPENAI_COMPAT_BASE_URL)


def provider_name() -> str:
    """Return the active provider label for UI display."""
    return LLM_PROVIDER
