"""Tests unitaires pour le wrapper Mistral, sans appel réseau réel."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from domestique_ai.llm.assistant import MISTRAL_API_URL, MistralAssistant


def test_init_raises_when_no_api_key(monkeypatch):
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="MISTRAL_API_KEY"):
        MistralAssistant(api_key=None)


def test_ask_sends_bearer_header_and_returns_content():
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "choices": [{"message": {"content": "Tu as une bonne forme."}}]
    }
    fake_response.raise_for_status.return_value = None

    with patch("domestique_ai.llm.assistant.requests.post", return_value=fake_response) as post:
        assistant = MistralAssistant(api_key="sk-test-123")
        result = assistant.ask("Comment est ma forme ?", context="CTL=80, ATL=70, TSB=10")

    assert result == "Tu as une bonne forme."
    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0] == MISTRAL_API_URL
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test-123"
    assert kwargs["json"]["model"] == "mistral-large-latest"
    assert kwargs["json"]["messages"][0]["role"] == "system"
    assert kwargs["json"]["messages"][1]["content"] == "Comment est ma forme ?"
