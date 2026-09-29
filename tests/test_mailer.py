"""Tests du module d'envoi d'emails (SMTP, best-effort)."""

from __future__ import annotations

import smtplib

from domestique_ai import mailer


def test_send_email_noop_without_host(monkeypatch) -> None:
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert mailer.send_email("a@b.c", "sujet", "corps") is False


def test_send_verification_email_builds_absolute_link(monkeypatch) -> None:
    captured: dict[str, str] = {}
    monkeypatch.setenv("DOMESTIQUE_AI_APP_BASE_URL", "https://ex.com")
    monkeypatch.setattr(
        mailer,
        "send_email",
        lambda to, subject, body: captured.update(to=to, body=body) or True,
    )
    assert mailer.send_verification_email("a@b.c", "tok123") is True
    assert "https://ex.com/verify-email?token=tok123" in captured["body"]


def test_send_password_reset_email_builds_absolute_link(monkeypatch) -> None:
    captured: dict[str, str] = {}
    monkeypatch.setenv("DOMESTIQUE_AI_APP_BASE_URL", "https://ex.com")
    monkeypatch.setattr(
        mailer,
        "send_email",
        lambda to, subject, body: captured.update(to=to, body=body) or True,
    )
    assert mailer.send_password_reset_email("a@b.c", "tok123") is True
    assert "https://ex.com/reset-password?token=tok123" in captured["body"]


class _FakeSMTP:
    instances: list[_FakeSMTP] = []

    def __init__(self, host: str, port: int, timeout: int | None = None) -> None:
        self.host = host
        self.port = port
        self.logged_in: tuple[str, str] | None = None
        self.messages: list = []
        _FakeSMTP.instances.append(self)

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def ehlo(self) -> None: ...

    def starttls(self) -> None: ...

    def login(self, user: str, password: str) -> None:
        self.logged_in = (user, password)

    def send_message(self, message) -> None:
        self.messages.append(message)


def test_send_email_uses_smtp_and_auth(monkeypatch) -> None:
    _FakeSMTP.instances = []
    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setenv("SMTP_PORT", "2525")
    monkeypatch.setenv("SMTP_USER", "bot@test")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)

    assert mailer.send_email("to@b.c", "sujet", "corps") is True
    assert len(_FakeSMTP.instances) == 1
    smtp = _FakeSMTP.instances[0]
    assert (smtp.host, smtp.port) == ("smtp.test", 2525)
    assert smtp.logged_in == ("bot@test", "secret")
    assert smtp.messages[0]["To"] == "to@b.c"


def test_send_email_returns_false_on_smtp_error(monkeypatch) -> None:
    def _boom(*args: object, **kwargs: object):
        raise smtplib.SMTPException("down")

    monkeypatch.setenv("SMTP_HOST", "smtp.test")
    monkeypatch.setattr(smtplib, "SMTP", _boom)
    assert mailer.send_email("to@b.c", "sujet", "corps") is False
