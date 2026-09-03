"""Tests pour le module coach (boucle agentique).

Pas de test du LLM lui-même : on valide la sanitization du contexte
réinjecté à Ollama, qui doit ignorer les tool_calls / tool_responses
des tours passés (sinon Pydantic plante côté SDK).
"""

from __future__ import annotations

from domestique_ai.llm import coach as coach_module
from domestique_ai.llm.coach import SYSTEM_PROMPT, build_initial_messages


def test_build_initial_messages_strips_tool_calls_from_history(monkeypatch):
    monkeypatch.setattr(
        "domestique_ai.llm.daily_brief.build_coach_context",
        lambda *args, **kwargs: "",
    )
    history = [
        {"role": "user", "content": "Comment va ma forme ?"},
        {
            "role": "assistant",
            "content": "Tu es optimal.",
            # Format ToolTrace persisté en DB — ne doit PAS atterrir dans le
            # prompt envoyé au modèle (il viole le schéma Pydantic d'Ollama).
            "tool_calls": [
                {"name": "get_training_load_state", "arguments": {}, "result": {"available": True}},
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


def test_build_initial_messages_drops_empty_content(monkeypatch):
    monkeypatch.setattr(
        "domestique_ai.llm.daily_brief.build_coach_context",
        lambda *args, **kwargs: "",
    )
    history = [
        {"role": "user", "content": "ping"},
        {"role": "assistant", "content": ""},  # message vide
        {"role": "tool", "content": "noise"},  # rôle non transmis
    ]
    messages = build_initial_messages(history, "go")
    roles = [m["role"] for m in messages]
    # system + user("ping") + user("go")
    assert roles == ["system", "user", "user"]


def test_build_initial_messages_handles_none_history(monkeypatch):
    """Sans contexte injecté, on attend [system, user]."""
    # On neutralise l'injection contextuelle palier 2 (qui dépend de l'état DB).
    monkeypatch.setattr(
        "domestique_ai.llm.daily_brief.build_coach_context",
        lambda *args, **kwargs: "",
    )
    messages = build_initial_messages(None, "Salut")
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[1]["content"] == "Salut"


def test_build_initial_messages_injects_coach_context_when_history_empty(monkeypatch):
    """Palier 2 : nouvelle session → contexte courant en message system additionnel."""
    monkeypatch.setattr(
        "domestique_ai.llm.daily_brief.build_coach_context",
        lambda *args, **kwargs: "Contexte injecté : TSB +5.0",
    )
    messages = build_initial_messages(None, "Salut")
    roles = [m["role"] for m in messages]
    # system principal + system contexte + user.
    assert roles == ["system", "system", "user"]
    assert messages[0]["content"] == SYSTEM_PROMPT
    assert "TSB +5.0" in messages[1]["content"]


def test_build_initial_messages_does_not_inject_context_when_history_present(monkeypatch):
    """Sur les tours suivants, le contexte n'est PAS réinjecté."""
    monkeypatch.setattr(
        "domestique_ai.llm.daily_brief.build_coach_context",
        lambda *args, **kwargs: "Contexte injecté qui ne doit pas apparaître",
    )
    history = [{"role": "user", "content": "Comment va ma forme ?"}]
    messages = build_initial_messages(history, "Et demain ?")
    roles = [m["role"] for m in messages]
    # 1 system + 1 history + 1 user — aucune deuxième entrée system.
    assert roles == ["system", "user", "user"]


def test_build_initial_messages_tolerates_context_builder_failure(monkeypatch):
    """Si le builder de contexte explose (DB vide, Ollama KO), on ignore."""

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "domestique_ai.llm.daily_brief.build_coach_context",
        boom,
    )
    messages = build_initial_messages(None, "Salut")
    # Le coach démarre quand même, juste sans contexte injecté.
    assert [m["role"] for m in messages] == ["system", "user"]


# Variable conservée pour rétro-compat des imports — pourra être retirée
# quand on retypera test_coach.py.
_ = coach_module
