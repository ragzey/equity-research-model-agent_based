"""Shared OpenAI chat helper for research-desk agents."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("LLMClient")

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"


def llm_configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


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


def chat_text(
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0.0,
    timeout: int = 90,
    json_mode: bool = False,
) -> Optional[str]:
    """Return model text, or None when no API key / the call fails."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    body: Dict[str, Any] = {
        "model": os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
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
        response.raise_for_status()
        return str(response.json()["choices"][0]["message"]["content"]).strip()
    except Exception:
        logger.exception("LLM chat call failed.")
        return None


def chat_json(
    messages: List[Dict[str, str]],
    *,
    temperature: float = 0.0,
    timeout: int = 90,
) -> Optional[Dict[str, Any]]:
    text = chat_text(
        messages,
        temperature=temperature,
        timeout=timeout,
        json_mode=True,
    )
    return parse_json_object(text or "")
