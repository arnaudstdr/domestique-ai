"""CLI d'administration des identifiants — bootstrap et secours.

Usage (sur le Raspberry Pi, dans le conteneur) :

    docker compose exec app python -m domestique_ai.auth_cli list-users
    docker compose exec app python -m domestique_ai.auth_cli set-credentials \\
        --email moi@exemple.com
    docker compose exec app python -m domestique_ai.auth_cli enroll-totp
    docker compose exec app python -m domestique_ai.auth_cli reset-2fa

Cible par défaut : le coach bootstrap (propriétaire). ``--user <public_id>``
permet de viser un autre compte. Ces commandes tournent en local, hors réseau :
elles constituent la voie de secours si l'on est verrouillé dehors.
"""

from __future__ import annotations

import argparse
import getpass
import sqlite3
import sys

import qrcode

from domestique_ai import security
from domestique_ai.platform_db import (
    disable_totp,
    enable_totp,
    get_or_create_bootstrap_coach,
    get_user_by_public_id,
    get_user_credentials,
    init_platform_db,
    list_users,
    replace_recovery_codes,
    set_totp_secret,
    set_user_credentials,
)


def _resolve_target(args: argparse.Namespace) -> dict:
    init_platform_db()
    if args.user:
        user = get_user_by_public_id(args.user)
        if user is None:
            sys.exit(f"Aucun utilisateur avec public_id={args.user!r}.")
        return user
    return get_or_create_bootstrap_coach()


def _prompt_password(confirm: bool = True) -> str:
    password = getpass.getpass("Mot de passe : ").strip()
    if confirm:
        again = getpass.getpass("Confirmer : ").strip()
        if password != again:
            sys.exit("Les deux saisies ne correspondent pas.")
    try:
        security.assert_password_strength(password)
    except security.PasswordPolicyError as exc:
        sys.exit(str(exc))
    return password


def cmd_list_users(_args: argparse.Namespace) -> None:
    init_platform_db()
    for user in list_users():
        creds = get_user_credentials(user["id"]) or {}
        flags = []
        if user["is_bootstrap"]:
            flags.append("bootstrap")
        flags.append("2FA" if creds.get("totp_enabled") else "sans-2FA")
        print(
            f"{user['public_id'][:8]}  {user['role']:<8}  "
            f"{user['email'] or '—':<28}  {', '.join(flags)}"
        )


def cmd_set_credentials(args: argparse.Namespace) -> None:
    user = _resolve_target(args)
    email = args.email or input("Email : ").strip()
    password = args.password or _prompt_password()
    if args.password:
        try:
            security.assert_password_strength(password)
        except security.PasswordPolicyError as exc:
            sys.exit(str(exc))
    try:
        set_user_credentials(user["id"], email, security.hash_password(password))
    except sqlite3.IntegrityError:
        sys.exit(f"L'email {email!r} est déjà utilisé par un autre compte.")
    print(f"Identifiants définis pour {user['public_id'][:8]} ({email}).")


def cmd_enroll_totp(args: argparse.Namespace) -> None:
    user = _resolve_target(args)
    secret = security.new_totp_secret()
    set_totp_secret(user["id"], secret)
    enable_totp(user["id"])
    account = user.get("email") or user["public_id"][:8]
    uri = security.totp_uri(secret, account)

    print("Scanne ce QR code avec ton application d'authentification :\n")
    qr = qrcode.QRCode(border=1)
    qr.add_data(uri)
    qr.print_ascii(invert=True)
    print(f"\nClé manuelle : {secret}\n")

    codes = security.generate_recovery_codes()
    replace_recovery_codes(user["id"], [security.hash_recovery_code(c) for c in codes])
    print("Codes de secours (à conserver, affichés une seule fois) :")
    for code in codes:
        print(f"  {code}")
    print("\n2FA activée.")


def cmd_reset_2fa(args: argparse.Namespace) -> None:
    user = _resolve_target(args)
    disable_totp(user["id"])
    print(f"2FA désactivée pour {user['public_id'][:8]}. Réactive-la ensuite via l'app.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="domestique_ai.auth_cli",
        description="Administration des identifiants (bootstrap + secours).",
    )
    parser.add_argument("--user", help="public_id cible (défaut : coach bootstrap)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list-users", help="Liste les comptes et leur statut 2FA")
    p_list.set_defaults(func=cmd_list_users)

    p_set = sub.add_parser("set-credentials", help="Définit email + mot de passe")
    p_set.add_argument("--email", help="Email du compte (demandé sinon)")
    p_set.add_argument("--password", help="Mot de passe (demandé sans écho sinon)")
    p_set.set_defaults(func=cmd_set_credentials)

    p_enroll = sub.add_parser("enroll-totp", help="Génère un secret TOTP et l'active")
    p_enroll.set_defaults(func=cmd_enroll_totp)

    p_reset = sub.add_parser("reset-2fa", help="Désactive la 2FA (dépannage)")
    p_reset.set_defaults(func=cmd_reset_2fa)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
