"""Chat helper for research-desk reasoning agents (OpenAI or Gemini)."""

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
GEMINI_CHAT_URL = (
    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
)
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_MODEL = DEFAULT_OPENAI_MODEL
_MODEL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
_MAX_KEY_LENGTH = 512
PROVIDERS = ("openai", "gemini")

_API_KEY: ContextVar[Optional[str]] = ContextVar("desk_llm_api_key", default=None)
_MODEL: ContextVar[Optional[str]] = ContextVar("desk_llm_model", default=None)
_PROVIDER: ContextVar[Optional[str]] = ContextVar("desk_llm_provider", default=None)
_ENV_LOCK = threading.Lock()
_ENV_KEYS = (
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "LLM_PROVIDER",
)

MISSING_KEY_MESSAGE = (
    "This research desk requires an OpenAI or Gemini API key for agent reasoning. "
    "Paste a key in the GUI, pass --openai-api-key, or set OPENAI_API_KEY / "
    "GEMINI_API_KEY. WACC and DCF stay in Python; Competitive, Qualitative, "
    "the industry/macro analyst, the assumption architect, the assumption "
    "reviewer, the writer, and the independent auditor must call the model."
)


class LLMNotConfiguredError(RuntimeError):
    """A reasoning agent ran without credentials."""


class LLMCallError(RuntimeError):
    """The chat request failed or returned unusable content."""


def redact_secrets(text: str) -> str:
    """Strip API keys from logs and error strings."""
    if not text:
        return text
    redacted = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-***", str(text))
    redacted = re.sub(r"AIza[0-9A-Za-z_-]{8,}", "AIza***", redacted)
    redacted = re.sub(r"AQ\.[A-Za-z0-9_-]{8,}", "AQ.***", redacted)
    return re.sub(r"(Bearer\s+)\S+", r"\1***", redacted, flags=re.IGNORECASE)


def _clean_model(value: Optional[str]) -> Optional[str]:
    model = (value or "").strip()
    if not model:
        return None
    if not _MODEL_RE.fullmatch(model):
        raise ValueError("Model name contains invalid characters.")
    return model


def _clean_api_key(value: Optional[str]) -> str:
    key = (value or "").strip()
    if len(key) > _MAX_KEY_LENGTH:
        raise ValueError("API key is unexpectedly long.")
    return key


def _clean_provider(value: Optional[str]) -> Optional[str]:
    provider = (value or "").strip().lower()
    if not provider or provider == "auto":
        return None
    if provider not in PROVIDERS:
        raise ValueError("AI provider must be openai, gemini, or auto.")
    return provider


def infer_provider(
    api_key: str = "",
    model: str = "",
    explicit: Optional[str] = None,
) -> str:
    cleaned = _clean_provider(explicit)
    if cleaned:
        return cleaned
    model_l = (model or "").lower()
    if model_l.startswith("gemini"):
        return "gemini"
    key = api_key or ""
    if key.startswith("AIza") or key.startswith("AQ."):
        return "gemini"
    if key.startswith("sk-"):
        return "openai"
    if _clean_api_key(os.getenv("GEMINI_API_KEY")) or _clean_api_key(
        os.getenv("GOOGLE_API_KEY")
    ):
        if not _clean_api_key(os.getenv("OPENAI_API_KEY")):
            return "gemini"
    return "openai"


def _default_model(provider: str) -> str:
    if provider == "gemini":
        return (
            _clean_model(os.getenv("GEMINI_MODEL"))
            or DEFAULT_GEMINI_MODEL
        )
    return _clean_model(os.getenv("OPENAI_MODEL")) or DEFAULT_OPENAI_MODEL


def _coerce_model(provider: str, model: Optional[str]) -> str:
    cleaned = _clean_model(model)
    if provider == "gemini":
        if cleaned and cleaned.lower().startswith("gemini"):
            return cleaned
        return _default_model("gemini")
    if cleaned and not cleaned.lower().startswith("gemini"):
        return cleaned
    return _default_model("openai")


def _env_api_key(provider: str) -> str:
    openai_key = _clean_api_key(os.getenv("OPENAI_API_KEY"))
    gemini_key = _clean_api_key(os.getenv("GEMINI_API_KEY")) or _clean_api_key(
        os.getenv("GOOGLE_API_KEY")
    )
    if provider == "gemini":
        return gemini_key or openai_key
    return openai_key or gemini_key


def resolve_llm() -> Tuple[str, str, str]:
    ctx_key = _clean_api_key(_API_KEY.get())
    ctx_model = _clean_model(_MODEL.get())
    explicit = _PROVIDER.get() or os.getenv("LLM_PROVIDER")
    probe_key = ctx_key or _clean_api_key(os.getenv("OPENAI_API_KEY")) or _clean_api_key(
        os.getenv("GEMINI_API_KEY")
    ) or _clean_api_key(os.getenv("GOOGLE_API_KEY"))
    provider = infer_provider(probe_key, ctx_model or "", explicit)
    key = ctx_key or _env_api_key(provider)
    model = _coerce_model(provider, ctx_model)
    return key, model, provider


def resolve_credentials() -> Tuple[str, str]:
    key, model, _provider = resolve_llm()
    return key, model


def llm_configured() -> bool:
    return bool(resolve_llm()[0])


def require_llm() -> Tuple[str, str]:
    key, model, _provider = resolve_llm()
    if not key:
        raise LLMNotConfiguredError(MISSING_KEY_MESSAGE)
    return key, model


def chat_url_for(provider: str) -> str:
    if provider == "gemini":
        return GEMINI_CHAT_URL
    return OPENAI_CHAT_URL


@contextmanager
def llm_session(
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    provider: Optional[str] = None,
) -> Iterator[None]:
    """Use a per-run key/model without writing them to disk."""
    cleaned_key = _clean_api_key(api_key)
    cleaned_model = _clean_model(model)
    cleaned_provider = _clean_provider(provider)
    if not cleaned_key and not cleaned_model and not cleaned_provider:
        yield
        return

    _ENV_LOCK.acquire()
    previous = {name: os.environ.get(name) for name in _ENV_KEYS}
    tokens: List[Tuple[ContextVar[Optional[str]], Any]] = []
    try:
        if cleaned_key:
            tokens.append((_API_KEY, _API_KEY.set(cleaned_key)))
        if cleaned_model:
            tokens.append((_MODEL, _MODEL.set(cleaned_model)))
        if cleaned_provider:
            tokens.append((_PROVIDER, _PROVIDER.set(cleaned_provider)))
        key, resolved_model, resolved_provider = resolve_llm()
        os.environ["LLM_PROVIDER"] = resolved_provider
        if resolved_provider == "gemini":
            if key:
                os.environ["GEMINI_API_KEY"] = key
            os.environ["GEMINI_MODEL"] = resolved_model
        else:
            if key:
                os.environ["OPENAI_API_KEY"] = key
            os.environ["OPENAI_MODEL"] = resolved_model
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
        for name in _ENV_KEYS:
            prior = previous[name]
            if prior is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = prior
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


def _error_from_response(response: requests.Response, provider: str) -> str:
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
    label = "Gemini" if provider == "gemini" else "OpenAI"
    if response.status_code == 401:
        return (
            f"{label} rejected the API key. Check the GUI key, OPENAI_API_KEY, "
            "or GEMINI_API_KEY."
        )
    if response.status_code == 404:
        return redact_secrets(
            f"{label} could not find that model. {message}".strip()
        )
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
    api_key, model, provider = resolve_llm()
    if not api_key:
        if required:
            raise LLMNotConfiguredError(MISSING_KEY_MESSAGE)
        return None
    url = chat_url_for(provider)
    use_json_mode = json_mode
    last_error = f"{provider} request failed."
    for attempt in range(2):
        body: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if use_json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            response = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=timeout,
            )
            if (
                use_json_mode
                and response.status_code == 400
                and "json" in (response.text or "").lower()
            ):
                use_json_mode = False
                continue
            if response.status_code in {429, 500, 502, 503} and attempt == 0:
                time.sleep(1.0)
                continue
            if not response.ok:
                last_error = _error_from_response(response, provider)
                if required:
                    raise LLMCallError(last_error)
                logger.error("LLM chat call failed: %s", last_error)
                return None
            content = str(response.json()["choices"][0]["message"]["content"]).strip()
            if not content:
                last_error = f"{provider} returned an empty response."
                if required:
                    raise LLMCallError(last_error)
                return None
            return content
        except LLMCallError:
            raise
        except Exception as exc:
            last_error = redact_secrets(str(exc) or f"{provider} request failed.")
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
            raise LLMCallError("The model did not return a JSON object.")
        return None
    return parsed
