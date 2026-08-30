"""LLM session, required calls, and secret redaction."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from main import run_pipeline
from equity_research.utils.llm_client import (
    GEMINI_CHAT_URL,
    LLMCallError,
    LLMNotConfiguredError,
    chat_json,
    chat_text,
    infer_provider,
    llm_configured,
    llm_session,
    redact_secrets,
    require_llm,
    resolve_llm,
)


class LLMClientTests(unittest.TestCase):
    def test_redact_strips_keys_and_bearer_tokens(self):
        text = "Authorization: Bearer sk-abc123456789 and sk-proj-abcdefghij"
        redacted = redact_secrets(text)
        self.assertNotIn("sk-abc123456789", redacted)
        self.assertNotIn("sk-proj-abcdefghij", redacted)
        self.assertIn("sk-***", redacted)
        self.assertIn("Bearer ***", redacted)

    def test_redact_strips_gemini_keys(self):
        text = "key AIzaSyDummyKeyValue99 and AQ.secretTokenValue"
        redacted = redact_secrets(text)
        self.assertNotIn("AIzaSyDummyKeyValue99", redacted)
        self.assertNotIn("AQ.secretTokenValue", redacted)

    def test_infer_provider_from_key_and_model(self):
        self.assertEqual(infer_provider("sk-abc", "gpt-4o-mini"), "openai")
        self.assertEqual(infer_provider("AIzaSySomething", "gpt-4o-mini"), "gemini")
        self.assertEqual(infer_provider("sk-abc", "gemini-2.5-flash"), "gemini")
        self.assertEqual(infer_provider("", "", "gemini"), "gemini")

    @patch.dict(
        os.environ,
        {
            "OPENAI_API_KEY": "",
            "OPENAI_MODEL": "",
            "GEMINI_API_KEY": "",
            "GOOGLE_API_KEY": "",
        },
        clear=False,
    )
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

    @patch("equity_research.utils.llm_client.requests.post")
    @patch.dict(
        os.environ,
        {"OPENAI_API_KEY": "", "GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""},
        clear=False,
    )
    def test_gemini_session_posts_to_google_endpoint(self, mock_post):
        response = MagicMock()
        response.status_code = 200
        response.ok = True
        response.json.return_value = {
            "choices": [{"message": {"content": "hello"}}]
        }
        mock_post.return_value = response
        with llm_session(api_key="AIzaSyDummyKey12", provider="gemini"):
            key, model, provider = resolve_llm()
            self.assertEqual(provider, "gemini")
            self.assertTrue(model.startswith("gemini"))
            text = chat_text([{"role": "user", "content": "hi"}], required=True)
        self.assertEqual(text, "hello")
        self.assertEqual(mock_post.call_args.args[0], GEMINI_CHAT_URL)

    @patch.dict(
        os.environ,
        {"OPENAI_API_KEY": "", "GEMINI_API_KEY": "", "GOOGLE_API_KEY": ""},
        clear=False,
    )
    def test_pipeline_refuses_to_run_without_a_key(self):
        with self.assertRaises(LLMNotConfiguredError):
            run_pipeline("MSFT", "2026")


if __name__ == "__main__":
    unittest.main()
