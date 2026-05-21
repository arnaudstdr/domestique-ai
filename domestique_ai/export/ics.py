"""
Export d'un plan d'entraînement au format iCalendar (RFC 5545).

Génère un fichier ``.ics`` que les calendriers du quotidien (Google Calendar,
Apple Calendar, Outlook) savent importer. Implémentation manuelle sans
dépendance externe : ~80 lignes pour la sous-partie de la RFC qui nous
intéresse (VEVENT + VCALENDAR + folding + escaping).

Convention : les ``DTSTART`` sont émis en **floating local time** (sans
``TZID`` ni suffixe ``Z``) — le calendrier les interprète dans la timezone
de l'utilisateur, ce qui est exactement le comportement voulu (« lundi 18 h
chez moi »).
"""

from __future__ import annotations

import datetime as _dt

from domestique_ai.processing.plan_builder import Workout, WorkoutStep

# Producteur : exposé en `PRODID`, identifie l'app dans les clients calendrier.
_PRODID = "-//domestique-ai//Plan d'entrainement//FR"

# Créneau par défaut pour les séances : 18 h locale. À terme on pourra le lire
# depuis ``availability.yaml`` (préférences par jour), mais le MVP fixe une
# valeur unique car les clients calendrier permettent de déplacer une fois pour
# toutes les séances après import.
_DEFAULT_HOUR = 18


def _escape_text(value: str) -> str:
    """Escape RFC 5545 pour les valeurs ``TEXT`` (SUMMARY, DESCRIPTION, …).

    Ordre important : on échappe ``\\`` *avant* tout le reste pour ne pas
    re-traiter les antislashes qu'on vient d'introduire.
    """
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold_line(line: str) -> str:
    """Repliage RFC 5545 § 3.1 : pas plus de 75 octets par ligne logique.

    On compte en octets (UTF-8) parce qu'un accent peut faire basculer une
    ligne au-dessus de la limite alors que ``len()`` côté Python compte des
    caractères. Les segments de continuation commencent par un espace.
    """
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    parts: list[bytes] = []
    chunk_size = 75
    parts.append(encoded[:chunk_size])
    pos = chunk_size
    # 74 octets utiles + 1 octet pour l'espace de continuation = 75 max.
    while pos < len(encoded):
        parts.append(encoded[pos : pos + 74])
        pos += 74
    return "\r\n ".join(part.decode("utf-8") for part in parts)


def _format_dt_floating(date_iso: str, hour: int, minute: int = 0) -> str:
    """``"2026-05-21"`` + (18, 0) → ``"20260521T180000"`` (floating local time)."""
    d = _dt.date.fromisoformat(date_iso)
    return f"{d:%Y%m%d}T{hour:02d}{minute:02d}00"


def _format_dt_utc(moment: _dt.datetime) -> str:
    """Format ``DTSTAMP`` : ``20260521T143000Z`` (toujours en UTC)."""
    return moment.astimezone(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _format_duration(duration_min: int) -> str:
    """``90`` → ``"PT1H30M"`` (RFC 5545 § 3.3.6)."""
    hours, minutes = divmod(max(0, int(duration_min)), 60)
    if hours and minutes:
        return f"PT{hours}H{minutes}M"
    if hours:
        return f"PT{hours}H"
    return f"PT{minutes}M"


def _describe_step(step: WorkoutStep) -> str:
    """Une ligne lisible par séance — utilisée dans DESCRIPTION."""
    minutes = round(step.duration_sec / 60)
    label = {
        "warmup": "Échauffement",
        "active": "Actif",
        "rest": "Récup",
        "cooldown": "Retour au calme",
    }.get(step.phase, step.phase)
    if step.repeat > 1:
        return f"{step.repeat} × {minutes} min {label} {step.zone.upper()}"
    return f"{minutes} min {label} {step.zone.upper()}"


def _build_description(workout: Workout) -> str:
    """Concatène structure + TSS estimé en une chaîne avec sauts de ligne."""
    lines: list[str] = []
    for step in workout.structure:
        lines.append(_describe_step(step))
    if workout.estimated_tss:
        lines.append(f"TSS estimé : {workout.estimated_tss:.0f}")
    if workout.notes:
        lines.append(f"Notes : {workout.notes}")
    return "\n".join(lines)


def _build_event(
    workout: Workout,
    plan_id: int,
    default_hour: int,
    dtstamp: str,
) -> list[str]:
    """Liste de lignes VEVENT (avant folding) pour une séance."""
    uid = f"plan-{plan_id}-{workout.date}@domestique-ai"
    dtstart = _format_dt_floating(workout.date, default_hour, 0)
    duration = _format_duration(workout.duration_min)
    summary = _escape_text(workout.name)
    description = _escape_text(_build_description(workout))
    return [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{dtstart}",
        f"DURATION:{duration}",
        f"SUMMARY:{summary}",
        f"DESCRIPTION:{description}",
        f"CATEGORIES:Entrainement,{workout.kind}",
        "END:VEVENT",
    ]


def plan_to_ics(
    plan: list[Workout],
    plan_id: int,
    default_hour: int = _DEFAULT_HOUR,
    now: _dt.datetime | None = None,
) -> bytes:
    """Sérialise un plan en ``text/calendar`` (UTF-8).

    Args:
        plan : liste de séances ordonnée chronologiquement.
        plan_id : identifiant SQLite du plan, utilisé dans l'UID stable.
        default_hour : heure locale par défaut des séances (18 h par défaut).
        now : horodatage ``DTSTAMP`` (en UTC). Optionnel — utile pour les tests.

    Returns:
        Le fichier ``.ics`` complet en bytes, prêt à être renvoyé par
        ``fastapi.Response`` avec ``media_type="text/calendar"``.
    """
    dtstamp = _format_dt_utc(now or _dt.datetime.now(_dt.timezone.utc))
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for workout in plan:
        lines.extend(_build_event(workout, plan_id, default_hour, dtstamp))
    lines.append("END:VCALENDAR")
    folded = [_fold_line(line) for line in lines]
    # RFC 5545 § 3.1 : terminer chaque ligne par CRLF + un dernier CRLF final.
    payload = "\r\n".join(folded) + "\r\n"
    return payload.encode("utf-8")
