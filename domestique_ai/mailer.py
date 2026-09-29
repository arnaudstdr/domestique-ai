"""Envoi d'emails transactionnels (vérification, mot de passe oublié) via SMTP.

Best-effort : un échec d'envoi n'altère jamais le flux appelant (inscription,
demande de reset) — il est loggé et signalé par un retour ``False``. Si
``SMTP_HOST`` est absent, l'envoi est un no-op loggé (dev local : l'inscription
reste possible, l'utilisateur ne reçoit simplement pas le mail).
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Any

from domestique_ai import config

log = logging.getLogger(__name__)

_SMTP_TIMEOUT_S = 10

_FEEDBACK_CATEGORIES = {
    "bug": "Bug",
    "idea": "Idée",
    "remark": "Remarque",
    "other": "Autre",
}


def send_email(to: str, subject: str, body: str) -> bool:
    """Envoie un email texte via SMTP. ``False`` si non configuré ou en échec."""
    host = config.get_smtp_host()
    if not host:
        log.warning("SMTP_HOST absent — email non envoyé (to=%s, sujet=%s)", to, subject)
        return False
    sender = config.get_smtp_from() or "no-reply@localhost"
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    try:
        with smtplib.SMTP(host, config.get_smtp_port(), timeout=_SMTP_TIMEOUT_S) as smtp:
            if config.get_smtp_starttls():
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
            user = config.get_smtp_user()
            password = config.get_smtp_password()
            if user and password:
                smtp.login(user, password)
            smtp.send_message(message)
        return True
    except OSError:
        log.exception("Échec de connexion SMTP (%s) pour l'envoi à %s", host, to)
        return False
    except smtplib.SMTPException:
        log.exception("Échec d'envoi SMTP à %s", to)
        return False


def _link(path: str) -> str:
    return f"{config.get_app_base_url()}{path}"


def send_verification_email(to: str, token: str) -> bool:
    """Envoie le lien de vérification d'adresse (``/verify-email?token=…``)."""
    link = _link(f"/verify-email?token={token}")
    body = (
        "Bienvenue sur DomestiqueAI.\n\n"
        "Confirme ton adresse email en ouvrant ce lien :\n"
        f"{link}\n\n"
        "Si tu n'es pas à l'origine de cette inscription, ignore cet email.\n"
    )
    return send_email(to, "Confirme ton adresse — DomestiqueAI", body)


def send_password_reset_email(to: str, token: str) -> bool:
    """Envoie le lien de réinitialisation de mot de passe (``/reset-password?token=…``)."""
    link = _link(f"/reset-password?token={token}")
    body = (
        "Une réinitialisation de mot de passe a été demandée pour ce compte.\n\n"
        "Choisis un nouveau mot de passe en ouvrant ce lien :\n"
        f"{link}\n\n"
        "Le lien expire bientôt. Si tu n'es pas à l'origine de cette demande, "
        "ignore cet email : ton mot de passe actuel reste inchangé.\n"
    )
    return send_email(to, "Réinitialise ton mot de passe — DomestiqueAI", body)


def send_feedback_notification(to: str, feedback: dict[str, Any]) -> bool:
    """Notifie par email un nouveau retour utilisateur (best-effort)."""
    category = _FEEDBACK_CATEGORIES.get(
        feedback.get("category") or "", feedback.get("category") or "—"
    )
    author = feedback.get("public_id") or "utilisateur inconnu"
    email = feedback.get("author_email")
    lines = [
        f"Catégorie : {category}",
        f"Auteur : {author}" + (f" <{email}>" if email else ""),
        f"Rôle : {feedback.get('role') or '—'}",
        f"Date : {feedback.get('created_at') or '—'}",
    ]
    if feedback.get("page"):
        lines.append(f"Page : {feedback['page']}")
    if feedback.get("app_version"):
        lines.append(f"Version : {feedback['app_version']}")
    body = "\n".join(lines) + "\n\n" + (feedback.get("message") or "") + "\n"
    return send_email(to, f"[DomestiqueAI][Feedback] {category} — {author}", body)
