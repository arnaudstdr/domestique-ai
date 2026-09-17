"""Tests de la CLI d'administration des identifiants (lot 8)."""

from __future__ import annotations

import pytest

from domestique_ai import auth_cli, security
from domestique_ai import platform_db as pdb


def test_list_users_runs(capsys: pytest.CaptureFixture) -> None:
    pdb.get_or_create_bootstrap_coach()
    auth_cli.main(["list-users"])
    out = capsys.readouterr().out
    assert "coach" in out
    assert "bootstrap" in out


def test_set_credentials_then_lookup() -> None:
    bootstrap = pdb.get_or_create_bootstrap_coach()
    auth_cli.main(["set-credentials", "--email", "coach@example.com", "--password", "coachsecret1"])
    user = pdb.get_user_by_email("coach@example.com")
    assert user is not None and user["id"] == bootstrap["id"]
    creds = pdb.get_user_credentials(bootstrap["id"])
    assert security.verify_password(creds["password_hash"], "coachsecret1")


def test_set_credentials_duplicate_email_exits(capsys: pytest.CaptureFixture) -> None:
    pdb.get_or_create_bootstrap_coach()
    owner = pdb.create_user(role="athlete")
    pdb.set_user_credentials(owner["id"], "taken@example.com", "hash")
    # Cible le bootstrap (autre compte) avec un email déjà pris → conflit.
    with pytest.raises(SystemExit):
        auth_cli.main(
            [
                "set-credentials",
                "--email",
                "taken@example.com",
                "--password",
                "coachsecret1",
            ]
        )


def test_enroll_totp_enables_and_creates_recovery_codes(capsys: pytest.CaptureFixture) -> None:
    bootstrap = pdb.get_or_create_bootstrap_coach()
    auth_cli.main(["enroll-totp"])
    out = capsys.readouterr().out
    assert "2FA activée" in out
    assert "Codes de secours" in out
    creds = pdb.get_user_credentials(bootstrap["id"])
    assert creds["totp_enabled"] is True
    assert len(pdb.list_recovery_codes(bootstrap["id"])) == security.RECOVERY_CODE_COUNT


def test_reset_2fa_disables() -> None:
    bootstrap = pdb.get_or_create_bootstrap_coach()
    auth_cli.main(["enroll-totp"])
    assert pdb.get_user_credentials(bootstrap["id"])["totp_enabled"] is True
    auth_cli.main(["reset-2fa"])
    assert pdb.get_user_credentials(bootstrap["id"])["totp_enabled"] is False


def test_unknown_user_exits() -> None:
    with pytest.raises(SystemExit):
        auth_cli.main(["--user", "does-not-exist", "enroll-totp"])
