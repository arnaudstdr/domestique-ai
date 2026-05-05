"""Tests pour le module coach (boucle agentique).

Pas de test du LLM lui-même : on valide la sanitization du contexte
réinjecté à Ollama, qui doit ignorer les tool_calls / tool_responses
des tours passés (sinon Pydantic plante côté SDK).
"""

from __future__ import annotations

from domestique_ai.llm.coach import SYSTEM_PROMPT, build_initial_messages


def test_build_initial_messages_strips_tool_calls_from_history():
    history = [
        {"role": "user", "content": "Comment va ma forme ?"},
        {
            "role": "assistant",
            "content": "Tu es optimal.",
            # Format ToolTrace persisté en DB — ne doit PAS atterrir dans le
            # prompt envoyé au modèle (il viole le schéma Pydantic d'Ollama).
            "tool_calls": [
                {"name": "get_training_load_state", "arguments": {},
                 "result": {"available": True}},
            ],
        },
    ]
    messages = build_initial_messages(history, "Et demain ?")

    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == SYSTEM_PROMPT
    # 1 system + 2 history + 1 user = 4
    assert len(messages) == 4
    for msg in messages:
        # Le LLM reçoit uniquement role + content, jamais de tool_calls.
        assert set(msg.keys()) == {"role", "content"}


def test_build_initial_messages_drops_empty_content():
    history = [
        {"role": "user", "content": "ping"},
        {"role": "assistant", "content": ""},  # message vide
        {"role": "tool", "content": "noise"},  # rôle non transmis
    ]
    messages = build_initial_messages(history, "go")
    roles = [m["role"] for m in messages]
    # system + user("ping") + user("go")
    assert roles == ["system", "user", "user"]


def test_build_initial_messages_handles_none_history():
    messages = build_initial_messages(None, "Salut")
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[1]["content"] == "Salut"
