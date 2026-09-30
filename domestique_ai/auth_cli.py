"""CLI d'administration des identifiants — bootstrap et secours.

Usage (sur le Raspberry Pi, dans le conteneur) :

    docker compose exec app python -m domestique_ai.auth_cli list-users
    docker compose exec app python -m domestique_ai.auth_cli create-user \\
        --role admin --email admin@exemple.com
    docker compose exec app python -m domestique_ai.auth_cli set-credentials \\
        --email moi@exemple.com
    docker compose exec app python -m domestique_ai.auth_cli enroll-totp
    docker compose exec app python -m domestique_ai.auth_cli reset-2fa
    docker compose exec app python -m domestique_ai.auth_cli set-role admin \\
        --user <public_id>

Cible par défaut : le coach bootstrap (propriétaire). ``--user <public_id>``
permet de viser un autre compte. Ces commandes tournent en local, hors réseau :
elles constituent la voie de secours si l'on est verrouillé dehors. La promotion
``admin`` (rôle isolé, panneau ``/api/admin``) se fait **uniquement** ici :
``create-user --role admin`` crée un compte admin à part (jamais via l'UI),
``set-role admin`` promeut un compte existant.
"""

from __future__ import annotations

import argparse
import getpass
import sqlite3
import sys

import qrcode

from domestique_ai import security
from domestique_ai.platform_db import (
    ALL_ROLES,
    create_account,
    disable_totp,
    enable_totp,
    get_or_create_bootstrap_coach,
    get_user_by_email,
    get_user_by_public_id,
    get_user_credentials,
    init_platform_db,
    list_users,
    replace_recovery_codes,
    set_totp_secret,
    set_user_credentials,
    set_user_role,
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


def cmd_create_user(args: argparse.Namespace) -> None:
    """Crée un compte hors-ligne (dont ``admin``), avec email + mot de passe."""
    init_platform_db()
    if args.role not in ALL_ROLES:
        sys.exit(f"Rôle invalide : {args.role!r} (attendu : {', '.join(ALL_ROLES)}).")
    email = (args.email or input("Email : ")).strip()
    if not email:
        sys.exit("Email requis.")
    if get_user_by_email(email) is not None:
        sys.exit(f"L'email {email!r} est déjà utilisé par un autre compte.")
    password = args.password or _prompt_password()
    if args.password:
        try:
            security.assert_password_strength(password)
        except security.PasswordPolicyError as exc:
            sys.exit(str(exc))
    try:
        user = create_account(
            args.role,
            display_name=(args.display_name or "").strip() or None,
            email=email,
            password_hash=security.hash_password(password),
        )
    except sqlite3.IntegrityError:
        sys.exit(f"L'email {email!r} est déjà utilisé par un autre compte.")

    # Un coach/athlète a son espace de données ; l'admin est isolé (pas d'espace).
    if args.role != "admin":
        from domestique_ai.athlete_context import context_for_athlete
        from domestique_ai.ingestion.db import init_db

        try:
            ctx = context_for_athlete(user)
            ctx.db_path.parent.mkdir(parents=True, exist_ok=True)
            init_db(ctx.db_path)
        except OSError:
            pass

    print(f"Compte {args.role} créé : {user['public_id']} ({email}).")
    print(f"→ Active la 2FA : auth_cli enroll-totp --user {user['public_id']}")


def cmd_list_users(_args: argparse.Namespace) -> None:
    init_platform_db()
    for user in list_users():
        creds = get_user_credentials(user["id"]) or {}
        flags = []
        if user["is_bootstrap"]:
            flags.append("bootstrap")
        flags.append("2FA" if creds.get("totp_enabled") else "sans-2FA")
        # public_id complet (32 car.) : nécessaire pour `--user <public_id>`.
        print(
            f"{user['public_id']}  {user['role']:<8}  "
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


def cmd_set_role(args: argparse.Namespace) -> None:
    """Change le rôle d'un compte (seul chemin d'attribution de ``admin``)."""
    user = _resolve_target(args)
    if args.role not in ALL_ROLES:
        sys.exit(f"Rôle invalide : {args.role!r} (attendu : {', '.join(ALL_ROLES)}).")
    updated = set_user_role(user["public_id"], args.role)
    if updated is None:
        sys.exit(f"Aucun utilisateur avec public_id={user['public_id']!r}.")
    print(f"Rôle de {updated['public_id'][:8]} : {args.role}.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="domestique_ai.auth_cli",
        description="Administration des identifiants (bootstrap + secours).",
    )
    parser.add_argument(
        "--user",
        help="public_id cible (défaut : coach bootstrap). Avant la sous-commande.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list-users", help="Liste les comptes et leur statut 2FA")
    p_list.set_defaults(func=cmd_list_users)

    p_create = sub.add_parser("create-user", help="Crée un compte hors-ligne (dont admin)")
    p_create.add_argument("--role", choices=list(ALL_ROLES), default="athlete", help="Rôle")
    p_create.add_argument("--email", help="Email du compte (demandé sinon)")
    p_create.add_argument("--password", help="Mot de passe (demandé sans écho sinon)")
    p_create.add_argument("--display-name", dest="display_name", help="Nom affiché")
    p_create.set_defaults(func=cmd_create_user)

    p_set = sub.add_parser("set-credentials", help="Définit email + mot de passe")
    p_set.add_argument("--email", help="Email du compte (demandé sinon)")
    p_set.add_argument("--password", help="Mot de passe (demandé sans écho sinon)")
    p_set.set_defaults(func=cmd_set_credentials)

    p_enroll = sub.add_parser("enroll-totp", help="Génère un secret TOTP et l'active")
    p_enroll.set_defaults(func=cmd_enroll_totp)

    p_reset = sub.add_parser("reset-2fa", help="Désactive la 2FA (dépannage)")
    p_reset.set_defaults(func=cmd_reset_2fa)

    p_role = sub.add_parser("set-role", help="Change le rôle (coach|athlete|admin)")
    p_role.add_argument("role", choices=list(ALL_ROLES), help="Nouveau rôle")
    p_role.set_defaults(func=cmd_set_role)

    # ``--user`` est accepté avant *ou* après la sous-commande. ``SUPPRESS``
    # empêche la valeur par défaut de la sous-commande d'écraser l'option
    # globale quand seul le préfixe (`--user X enroll-totp`) est utilisé.
    for sp in (p_list, p_create, p_set, p_enroll, p_reset, p_role):
        sp.add_argument(
            "--user",
            default=argparse.SUPPRESS,
            help="public_id cible (défaut : coach bootstrap).",
        )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
