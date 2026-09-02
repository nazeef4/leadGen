"""Thin, dependency-light LLM client.

The app is fully functional without an API key — every caller of this module must
have a deterministic offline fallback.  Supported providers:

* ``openai``              - api.openai.com chat completions
* ``openai_compatible``   - anything OpenAI shaped (Ollama, LM Studio, vLLM, ...)
* ``anthropic``           - api.anthropic.com messages
* ``offline``             - disabled (default)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from ..config import Settings, get_settings

log = logging.getLogger("leadgen.llm")


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    @property
    def enabled(self) -> bool:
        return bool(self.settings.llm_api_key) and self.settings.llm_provider != "offline"

    def info(self) -> dict:
        return {
            "provider": self.settings.llm_provider,
            "model": self.settings.llm_model,
            "enabled": self.enabled,
            "base_url": self.settings.llm_base_url if self.enabled else None,
        }

    # ------------------------------------------------------------------ core
    def chat(
        self,
        user: str,
        system: str = "You are a helpful B2B sales strategist.",
        temperature: float = 0.7,
        max_tokens: int = 700,
        json_mode: bool = False,
    ) -> str:
        if not self.enabled:
            raise LLMError("LLM is not configured (set LEADGEN_LLM_API_KEY).")
        provider = self.settings.llm_provider
        timeout = httpx.Timeout(self.settings.llm_timeout)
        headers = {"content-type": "application/json"}
        try:
            if provider == "anthropic":
                headers.update(
                    {
                        "x-api-key": self.settings.llm_api_key,
                        "anthropic-version": "2023-06-01",
                    }
                )
                payload: dict[str, Any] = {
                    "model": self.settings.llm_model,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "system": system,
                    "messages": [{"role": "user", "content": user}],
                }
                if json_mode:
                    payload["messages"][0]["content"] = (
                        user + "\n\nRespond with a single valid JSON object and nothing else."
                    )
                url = f"{self.settings.llm_base_url.rstrip('/')}/messages"
                with httpx.Client(timeout=timeout) as client:
                    res = client.post(url, headers=headers, json=payload)
                res.raise_for_status()
                data = res.json()
                return "".join(
                    block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
                ).strip()

            # openai + openai_compatible
            headers["authorization"] = f"Bearer {self.settings.llm_api_key}"
            payload = {
                "model": self.settings.llm_model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
            url = f"{self.settings.llm_base_url.rstrip('/')}/chat/completions"
            with httpx.Client(timeout=timeout) as client:
                res = client.post(url, headers=headers, json=payload)
            res.raise_for_status()
            data = res.json()
            return data["choices"][0]["message"]["content"].strip()
        except httpx.HTTPStatusError as exc:  # pragma: no cover - network dependent
            raise LLMError(f"LLM HTTP {exc.response.status_code}: {exc.response.text[:300]}") from exc
        except httpx.HTTPError as exc:  # pragma: no cover - network dependent
            raise LLMError(f"LLM request failed: {exc}") from exc
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"Unexpected LLM response shape: {exc}") from exc

    def chat_json(self, user: str, system: str = "You respond only with JSON.", **kw) -> dict:
        raw = self.chat(user, system=system, json_mode=True, **kw)
        return parse_json(raw)


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json(raw: str) -> dict:
    """Best-effort JSON extraction — models love to wrap output in prose."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = _FENCE.search(raw)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    start, end = raw.find("{"), raw.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            pass
    return {}


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def reset_llm() -> None:
    global _client
    _client = None
