"""OpenAI chat helper for research-desk reasoning agents."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Dict, Iterator, List, Optional, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("LLMClient")

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"
_MODEL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
_MAX_KEY_LENGTH = 512

_API_KEY: ContextVar[Optional[str]] = ContextVar("desk_openai_api_key", default=None)
_MODEL: ContextVar[Optional[str]] = ContextVar("desk_openai_model", default=None)
_ENV_LOCK = threading.Lock()

MISSING_KEY_MESSAGE = (
    "This research desk requires an OpenAI API key for agent reasoning. "
    "Paste a key in the GUI, pass --openai-api-key, or set OPENAI_API_KEY. "
    "WACC and DCF stay in Python; Competitive, Qualitative, the assumption "
    "reviewer, and the writer must call the model."
)


class LLMNotConfiguredError(RuntimeError):
    """A reasoning agent ran without credentials."""


class LLMCallError(RuntimeError):
    """The OpenAI request failed or returned unusable content."""


def redact_secrets(text: str) -> str:
    """Strip API keys from logs and error strings."""
    if not text:
        return text
    redacted = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-***", str(text))
    return re.sub(r"(Bearer\s+)\S+", r"\1***", redacted, flags=re.IGNORECASE)


def _clean_model(value: Optional[str]) -> Optional[str]:
    model = (value or "").strip()
    if not model:
        return None
    if not _MODEL_RE.fullmatch(model):
        raise ValueError("OpenAI model name contains invalid characters.")
    return model


def _clean_api_key(value: Optional[str]) -> str:
    key = (value or "").strip()
    if len(key) > _MAX_KEY_LENGTH:
        raise ValueError("OpenAI API key is unexpectedly long.")
    return key


def resolve_credentials() -> Tuple[str, str]:
    key = _clean_api_key(_API_KEY.get()) or _clean_api_key(
        os.getenv("OPENAI_API_KEY", "")
    )
    model = (
        _clean_model(_MODEL.get())
        or _clean_model(os.getenv("OPENAI_MODEL", ""))
        or DEFAULT_MODEL
    )
    return key, model


def llm_configured() -> bool:
    return bool(resolve_credentials()[0])


def require_llm() -> Tuple[str, str]:
    key, model = resolve_credentials()
    if not key:
        raise LLMNotConfiguredError(MISSING_KEY_MESSAGE)
    return key, model


@contextmanager
def llm_session(
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> Iterator[None]:
    """Use a per-run key/model without writing them to disk.

    LangGraph may run Competitive and Qualitative on worker threads, so the
    session also overlays process env for the duration of the run, then restores
    the previous values.
    """
    cleaned_key = _clean_api_key(api_key)
    cleaned_model = _clean_model(model)
    tokens: List[Tuple[ContextVar[Optional[str]], Any]] = []
    if not cleaned_key and not cleaned_model:
        yield
        return

    _ENV_LOCK.acquire()
    previous_key = os.environ.get("OPENAI_API_KEY")
    previous_model = os.environ.get("OPENAI_MODEL")
    try:
        if cleaned_key:
            tokens.append((_API_KEY, _API_KEY.set(cleaned_key)))
            os.environ["OPENAI_API_KEY"] = cleaned_key
        if cleaned_model:
            tokens.append((_MODEL, _MODEL.set(cleaned_model)))
            os.environ["OPENAI_MODEL"] = cleaned_model
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
        if cleaned_key:
            if previous_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = previous_key
        if cleaned_model:
            if previous_model is None:
                os.environ.pop("OPENAI_MODEL", None)
            else:
                os.environ["OPENAI_MODEL"] = previous_model
        _ENV_LOCK.release()


def parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    if not text or not str(text).strip():
        return None
    payload = str(text).strip()
    if payload.startswith("```"):
        payload = re.sub(r"^```(?:json)?\s*", "", payload)
        payload = re.sub(r"\s*```$", "", payload)
    try:
        value = json.loads(payload)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        start = payload.find("{")
        end = payload.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(payload[start : end + 1])
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            return None


def _error_from_response(response: requests.Response) -> str:
    try:
        data = response.json()
        message = ""
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                message = str(err.get("message") or "")
            elif err:
                message = str(err)
        message = message or response.reason or f"HTTP {response.status_code}"
    except Exception:
        message = response.reason or f"HTTP {response.status_code}"
    if response.status_code == 401:
        return "OpenAI rejected the API key. Check the key in the GUI or OPENAI_API_KEY."
    return redact_secrets(message)


def chat_text(
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0.0,
    timeout: int = 90,
    json_mode: bool = False,
    required: bool = False,
) -> Optional[str]:
    """Return model text. required=True raises instead of returning None."""
    api_key, model = resolve_credentials()
    if not api_key:
        if required:
            raise LLMNotConfiguredError(MISSING_KEY_MESSAGE)
        return None
    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    last_error = "OpenAI request failed."
    for attempt in range(2):
        try:
            response = requests.post(
                OPENAI_CHAT_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=timeout,
            )
            if response.status_code in {429, 500, 502, 503} and attempt == 0:
                time.sleep(1.0)
                continue
            if not response.ok:
                last_error = _error_from_response(response)
                if required:
                    raise LLMCallError(last_error)
                logger.error("LLM chat call failed: %s", last_error)
                return None
            content = str(response.json()["choices"][0]["message"]["content"]).strip()
            if not content:
                last_error = "OpenAI returned an empty response."
                if required:
                    raise LLMCallError(last_error)
                return None
            return content
        except LLMCallError:
            raise
        except Exception as exc:
            last_error = redact_secrets(str(exc) or "OpenAI request failed.")
            logger.exception("LLM chat call failed.")
            if attempt == 0:
                time.sleep(0.5)
                continue
            if required:
                raise LLMCallError(last_error) from exc
            return None
    if required:
        raise LLMCallError(last_error)
    return None


def chat_json(
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0.0,
    timeout: int = 90,
    required: bool = False,
) -> Optional[Dict[str, Any]]:
    text = chat_text(
        messages,
        temperature=temperature,
        timeout=timeout,
        json_mode=True,
        required=required,
    )
    parsed = parse_json_object(text or "")
    if parsed is None:
        if required:
            raise LLMCallError("OpenAI did not return a JSON object.")
        return None
    return parsed
