"""LLM session, required calls, and secret redaction."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from main import run_pipeline
from equity_research.utils.llm_client import (
    LLMCallError,
    LLMNotConfiguredError,
    chat_json,
    chat_text,
    llm_configured,
    llm_session,
    redact_secrets,
    require_llm,
)


class LLMClientTests(unittest.TestCase):
    def test_redact_strips_keys_and_bearer_tokens(self):
        text = "Authorization: Bearer sk-abc123456789 and sk-proj-abcdefghij"
        redacted = redact_secrets(text)
        self.assertNotIn("sk-abc123456789", redacted)
        self.assertNotIn("sk-proj-abcdefghij", redacted)
        self.assertIn("sk-***", redacted)
        self.assertIn("Bearer ***", redacted)

    @patch.dict(os.environ, {"OPENAI_API_KEY": "", "OPENAI_MODEL": ""}, clear=False)
    def test_required_chat_raises_without_key(self):
        with llm_session(api_key=""):
            self.assertFalse(llm_configured())
            with self.assertRaises(LLMNotConfiguredError):
                require_llm()
            with self.assertRaises(LLMNotConfiguredError):
                chat_text(
                    [{"role": "user", "content": "hi"}],
                    required=True,
                )

    @patch.dict(os.environ, {"OPENAI_API_KEY": "env-key", "OPENAI_MODEL": "gpt-env"}, clear=False)
    def test_session_key_overrides_env_then_restores(self):
        with llm_session(api_key="sk-session-key", model="gpt-4o-mini"):
            self.assertTrue(llm_configured())
            key, model = require_llm()
            self.assertEqual(key, "sk-session-key")
            self.assertEqual(model, "gpt-4o-mini")
            self.assertEqual(os.environ["OPENAI_API_KEY"], "sk-session-key")
        self.assertEqual(os.environ["OPENAI_API_KEY"], "env-key")
        self.assertEqual(os.environ["OPENAI_MODEL"], "gpt-env")

    @patch("equity_research.utils.llm_client.requests.post")
    @patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False)
    def test_401_does_not_echo_the_key(self, mock_post):
        response = MagicMock()
        response.status_code = 401
        response.ok = False
        response.reason = "Unauthorized"
        response.json.return_value = {
            "error": {"message": "Incorrect API key provided: sk-secretkeyvalue"}
        }
        mock_post.return_value = response
        with llm_session(api_key="sk-secretkeyvalue"):
            with self.assertRaises(LLMCallError) as raised:
                chat_json(
                    [{"role": "user", "content": "{}"}],
                    required=True,
                )
        self.assertNotIn("sk-secretkeyvalue", str(raised.exception))

    @patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False)
    def test_pipeline_refuses_to_run_without_a_key(self):
        with self.assertRaises(LLMNotConfiguredError):
            run_pipeline("MSFT", "2026")


if __name__ == "__main__":
    unittest.main()
