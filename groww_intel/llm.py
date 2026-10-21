"""LLM provider abstraction.

A single, configurable, OpenAI-compatible chat-completions client (Groq by
default, OpenAI supported) with:

* explicit "no credentials" behaviour — the app degrades to deterministic,
  rule-based analysis instead of silently pretending an LLM ran,
* structured-JSON output with strict schema validation performed by the
  caller (see ``schemas.py``),
* retries with backoff for 429 / 5xx and clear, actionable error messages.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from .config import LLMConfig, load_llm_config


class LLMError(Exception):
    """Raised when the LLM call fails in a way the caller must surface."""


class LLMAuthError(LLMError):
    """Credentials missing or rejected."""


class LLMRateLimitError(LLMError):
    """Rate limited by the provider."""


@dataclass
class LLMResponse:
    text: str
    raw: dict
    model: str
    provider: str
    attempts: int = 1


class LLMClient:
    """Thin, dependency-light client for OpenAI-compatible chat completions."""

    def __init__(self, config: Optional[LLMConfig] = None) -> None:
        self.config = config or load_llm_config()

    # -- capability ------------------------------------------------------
    @property
    def available(self) -> bool:
        return self.config.available

    def describe(self) -> str:
        return self.config.describe()

    # -- calls -----------------------------------------------------------
    def complete(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        retries: int = 3,
    ) -> LLMResponse:
        """Run a chat completion and return the assistant message."""
        if not self.available:
            raise LLMAuthError(
                "No LLM credentials configured. Set GROQ_API_KEY (or OPENAI_API_KEY) "
                "in the environment or in Streamlit secrets."
            )

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.config.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.config.max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }

        last_error: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            try:
                with httpx.Client(timeout=self.config.timeout_seconds) as client:
                    response = client.post(url, headers=headers, json=payload)
                if response.status_code in (401, 403):
                    raise LLMAuthError(
                        f"{self.config.provider} rejected the API key (HTTP {response.status_code}). "
                        "Check the key in your environment / Streamlit secrets."
                    )
                if response.status_code == 429:
                    raise LLMRateLimitError(
                        f"{self.config.provider} rate limit reached (HTTP 429). Retry later or lower the batch size."
                    )
                if response.status_code >= 500:
                    raise LLMError(f"{self.config.provider} server error (HTTP {response.status_code}).")
                if response.status_code >= 400:
                    raise LLMError(
                        f"{self.config.provider} rejected the request (HTTP {response.status_code}): "
                        f"{response.text[:300]}"
                    )
                body = response.json()
                choices = body.get("choices") or []
                if not choices:
                    raise LLMError("LLM response contained no choices.")
                message = choices[0].get("message", {})
                content = message.get("content")
                if content is None:
                    raise LLMError("LLM response contained no message content.")
                return LLMResponse(
                    text=content.strip(),
                    raw=body,
                    model=body.get("model", self.config.model),
                    provider=self.config.provider,
                    attempts=attempt,
                )
            except (LLMAuthError, LLMRateLimitError):
                raise
            except LLMError as exc:
                last_error = exc
            except Exception as exc:  # network / timeout
                last_error = LLMError(f"Network error while calling {self.config.provider}: {exc}")

            if attempt < retries:
                time.sleep(1.5 * attempt)

        raise LLMError(f"LLM call failed after {retries} attempts: {last_error}")

    def complete_json(
        self,
        system: str,
        user: str,
        *,
        retries: int = 3,
    ) -> tuple[dict, LLMResponse]:
        """Run a completion that must return a JSON object.

        Tolerates models that wrap JSON in prose or code fences.
        """
        response = self.complete(system, user, json_mode=True, retries=retries)
        payload = extract_json_object(response.text)
        if payload is None:
            raise LLMError(
                "LLM did not return parsable JSON. Response started with: "
                f"{response.text[:200]!r}"
            )
        return payload, response


def extract_json_object(text: str) -> Optional[dict]:
    """Best-effort extraction of a JSON object from a model response."""
    if not text:
        return None
    candidate = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", candidate, re.DOTALL)
    if fence:
        candidate = fence.group(1).strip()
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(candidate[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            return None
    return None
