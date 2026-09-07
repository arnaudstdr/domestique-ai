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
from typing import Any
from zoneinfo import ZoneInfo

from domestique_ai.processing.plan_builder import Workout, WorkoutStep

# Producteur : exposé en `PRODID`, identifie l'app dans les clients calendrier.
_PRODID = "-//domestique-ai//Plan d'entrainement//FR"

# Créneau par défaut pour les séances : 18 h locale. À terme on pourra le lire
# depuis ``availability.yaml`` (préférences par jour), mais le MVP fixe une
# valeur unique car les clients calendrier permettent de déplacer une fois pour
# toutes les séances après import.
_DEFAULT_HOUR = 18

# UID des événements générés par ce module : préfixe commun à ``plan_to_ics``
# et au flux d'abonnement. Attention, ``plan_to_ics`` ajoute ``plan-<id>-``
# devant pour rester stable à travers la re-génération du même plan ; le flux
# webcal, lui, n'inclut **pas** le plan_id car le plan change à chaque revue
# hebdo (UID ``domestique-ai-<date>@domestique-ai`` → identifie la séance du
# jour quelle que soit la version du plan).
_UID_SUFFIX = "@domestique-ai"


def _escape_text(value: str) -> str:
    """Escape RFC 5545 pour les valeurs ``TEXT`` (SUMMARY, DESCRIPTION, …).

    Ordre important : on échappe ``\\`` *avant* tout le reste pour ne pas
    re-traiter les antislashes qu'on vient d'introduire.
    """
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold_line(line: str) -> str:
    """Repliage RFC 5545 § 3.1 : pas plus de 75 octets par ligne logique.

    On compte en octets (UTF-8) parce qu'un accent peut faire basculer une
    ligne au-dessus de la limite alors que ``len()`` côté Python compte des
    caractères. Les segments de continuation commencent par un espace
    (le ``CRLF + SPACE`` du séparateur compte comme 0 octet utile : on
    autorise donc 75 octets pour le 1er segment et 74 pour les suivants).

    On itère caractère par caractère pour ne **jamais** couper un codepoint
    UTF-8 multi-octets en deux (sinon ``decode("utf-8")`` plante avec
    ``UnicodeDecodeError`` quand un accent tombe sur une frontière).
    """
    if len(line.encode("utf-8")) <= 75:
        return line
    parts: list[str] = []
    current: list[str] = []
    current_bytes = 0
    limit = 75  # premier segment ; passe à 74 dès le 2e
    for ch in line:
        ch_bytes = len(ch.encode("utf-8"))
        if current_bytes + ch_bytes > limit and current:
            parts.append("".join(current))
            current = [ch]
            current_bytes = ch_bytes
            limit = 74
        else:
            current.append(ch)
            current_bytes += ch_bytes
    if current:
        parts.append("".join(current))
    return "\r\n ".join(parts)


def _format_dt_floating(date_iso: str, hour: int, minute: int = 0) -> str:
    """``"2026-05-21"`` + (18, 0) → ``"20260521T180000"`` (floating local time)."""
    d = _dt.date.fromisoformat(date_iso)
    return f"{d:%Y%m%d}T{hour:02d}{minute:02d}00"


def _format_dt_utc_for(
    date_iso: str, hour: int, duration_min: int, tz_name: str
) -> tuple[str, str]:
    """`DTSTART`/`DTEND` en UTC pour une séance à ``hour`` locale dans ``tz_name``.

    L'heure locale (18 h) est convertie en UTC via le fuseau ``tz_name``, et les
    deux bornes sont émises avec le suffixe ``Z``. Contrairement au *floating
    local time* (``plan_to_ics``), iCloud/Apple Calendar interprètent sans
    ambiguïté des timestamps UTC — un ``DTSTART`` sans fuseau peut être lu comme
    UTC par le serveur et décalé/masqué côté appareil.

    Retourne ``(dtstart, dtend)`` formatés (``"20260521T160000Z"``, …).
    """
    tz = ZoneInfo(tz_name)
    start = _dt.datetime.combine(_dt.date.fromisoformat(date_iso), _dt.time(hour, 0), tzinfo=tz)
    end = start + _dt.timedelta(minutes=max(0, int(duration_min)))
    return start.astimezone(_dt.UTC).strftime("%Y%m%dT%H%M%SZ"), end.astimezone(_dt.UTC).strftime(
        "%Y%m%dT%H%M%SZ"
    )


def _format_dt_end_floating(date_iso: str, hour: int, duration_min: int) -> str:
    """``DTEND`` en floating local time : ``DTSTART`` + durée.

    ``"2026-05-21"`` + 18 h + 90 min → ``"20260521T193000"``.
    """
    start = _dt.datetime.combine(_dt.date.fromisoformat(date_iso), _dt.time(hour, 0))
    end = start + _dt.timedelta(minutes=max(0, int(duration_min)))
    return end.strftime("%Y%m%dT%H%M%S")


def _format_dt_utc(moment: _dt.datetime) -> str:
    """Format ``DTSTAMP`` : ``20260521T143000Z`` (toujours en UTC)."""
    return moment.astimezone(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")


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


def workout_uid(workout: Workout, *, prefix: str = "domestique-ai-") -> str:
    """UID stable d'une séance — ``<prefix><date>@domestique-ai``.

    ``prefix`` est ``"domestique-ai-"`` par défaut (flux webcal : identifie la
    séance du jour quelle que soit la version du plan) ; ``plan_to_ics`` passe
    ``prefix=f"plan-{plan_id}-"`` pour ancrer l'UID à un plan précis.
    """
    return f"{prefix}{workout.date}{_UID_SUFFIX}"


def _build_event(
    workout: Workout,
    uid: str,
    default_hour: int,
    dtstamp: str,
    *,
    use_dtend: bool = False,
    tz_name: str | None = None,
) -> list[str]:
    """Liste de lignes VEVENT (avant folding) pour une séance.

    ``use_dtend=True`` émet un ``DTEND`` explicite au lieu de ``DURATION``.
    ``tz_name`` (nom IANA, ex. ``Europe/Paris``) émet ``DTSTART``/``DTEND`` en
    **UTC** (suffixe ``Z``) — le format fiable pour un flux consommé par les
    clients calendrier (Apple/Google).
    Sans ``tz_name`` ni ``use_dtend``, on reste en floating local time +
    ``DURATION`` (export de fichier ``plan_to_ics``).
    """
    if tz_name:
        dtstart, dtend = _format_dt_utc_for(
            workout.date, default_hour, workout.duration_min, tz_name
        )
        time_line = f"DTEND:{dtend}"
    else:
        dtstart = _format_dt_floating(workout.date, default_hour, 0)
        time_line = (
            f"DTEND:{_format_dt_end_floating(workout.date, default_hour, workout.duration_min)}"
            if use_dtend
            else f"DURATION:{_format_duration(workout.duration_min)}"
        )
    summary = _escape_text(workout.name)
    description = _escape_text(_build_description(workout))
    return [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{dtstart}",
        time_line,
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
    dtstamp = _format_dt_utc(now or _dt.datetime.now(_dt.UTC))
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for workout in plan:
        lines.extend(
            _build_event(
                workout, workout_uid(workout, prefix=f"plan-{plan_id}-"), default_hour, dtstamp
            )
        )
    lines.append("END:VCALENDAR")
    folded = [_fold_line(line) for line in lines]
    # RFC 5545 § 3.1 : terminer chaque ligne par CRLF + un dernier CRLF final.
    payload = "\r\n".join(folded) + "\r\n"
    return payload.encode("utf-8")


def workout_to_ics(
    workout: Workout,
    default_hour: int = _DEFAULT_HOUR,
    now: _dt.datetime | None = None,
    *,
    tz_name: str | None = None,
) -> bytes:
    """Sérialise une séance seule en ``text/calendar`` (UTF-8).

    Un seul VEVENT dont l'UID **ne dépend pas du plan_id**
    (``domestique-ai-<date>@domestique-ai``) — la revue hebdo change de plan à
    chaque fois, l'UID doit donc rester stable pour identifier la séance du
    jour et éviter les doublons.

    ``tz_name`` (nom IANA) émet les heures en **UTC** (suffixe ``Z``) : iCloud
    interprète mal un ``DTSTART`` *floating* (durée/heure ambiguës → événement
    décalé ou invisible côté Apple Calendar). Sans ``tz_name``, on garde le
    floating local time (18 h).
    """
    dtstamp = _format_dt_utc(now or _dt.datetime.now(_dt.UTC))
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    lines.extend(
        _build_event(
            workout, workout_uid(workout), default_hour, dtstamp, use_dtend=True, tz_name=tz_name
        )
    )
    lines.append("END:VCALENDAR")
    folded = [_fold_line(line) for line in lines]
    payload = "\r\n".join(folded) + "\r\n"
    return payload.encode("utf-8")


def plan_to_subscription_ics(
    plan: list[Workout],
    default_hour: int = _DEFAULT_HOUR,
    now: _dt.datetime | None = None,
    *,
    tz_name: str | None = None,
) -> bytes:
    """Sérialise plusieurs séances en un seul VCALENDAR — flux d'abonnement.

    Destiné à ``GET /api/plan/feed.ics`` (abonnement webcal) : mêmes garanties
    que ``workout_to_ics`` (UID stable ``domestique-ai-<date>@domestique-ai``,
    ``DTEND`` explicite, heures UTC quand ``tz_name`` est fourni) mais plusieurs
    événements dans le même fichier. Les clients calendrier (Apple/Google)
    pollent l'URL et voient la fenêtre évoluer sans doublons.
    """
    dtstamp = _format_dt_utc(now or _dt.datetime.now(_dt.UTC))
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{_PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for workout in plan:
        lines.extend(
            _build_event(
                workout,
                workout_uid(workout),
                default_hour,
                dtstamp,
                use_dtend=True,
                tz_name=tz_name,
            )
        )
    lines.append("END:VCALENDAR")
    folded = [_fold_line(line) for line in lines]
    payload = "\r\n".join(folded) + "\r\n"
    return payload.encode("utf-8")


# ---------------------------------------------------------------------------
# Sélection des séances pour une fenêtre calendrier
# ---------------------------------------------------------------------------

_DECISION_REST = "rest"
_DECISION_ADJUSTED = "adjusted"


def rolling_weeks_window(today: _dt.date, weeks: int = 2) -> tuple[_dt.date, _dt.date]:
    """Fenêtre de ``weeks`` semaines à partir du lundi de la semaine en cours.

    ``(lundi courant, lundi courant + 7×weeks − 1 jour)`` — par défaut la
    **semaine en cours + la semaine à venir** (14 jours). Utilisée par le flux
    d'abonnement webcal pour que le calendrier montre dès à présent les séances
    de la semaine en cours (pas seulement celle d'après).
    """
    this_monday = today - _dt.timedelta(days=today.weekday())
    return this_monday, this_monday + _dt.timedelta(days=7 * max(1, weeks) - 1)


def _apply_decisions(
    workouts: list[Workout], decisions: list[dict[str, Any]]
) -> dict[str, Workout | None]:
    """Fusionne le plan et les décisions du jour en une map ``date -> workout``.

    ``None`` = séance retirée (repos coach) ; un ``Workout`` remplacé pour une
    décision « allégée ». Les dates non décidées gardent la séance du plan.
    """
    by_date: dict[str, Workout | None] = {w.date: w for w in workouts}
    for d in decisions:
        date = d.get("date")
        if not date:
            continue
        if d.get("decision") == _DECISION_REST:
            by_date[date] = None
        elif d.get("decision") == _DECISION_ADJUSTED and d.get("workout"):
            by_date[date] = d["workout"]
    return by_date


def select_upcoming_workouts(
    workouts: list[Workout],
    start: _dt.date,
    end: _dt.date,
    decisions: list[dict[str, Any]] | None = None,
) -> list[Workout]:
    """Séances (sans ``None``) du plan tombant dans ``[start, end]``, décisions appliquées."""
    merged = _apply_decisions(workouts, decisions or [])
    start_iso, end_iso = start.isoformat(), end.isoformat()
    return [
        w for date, w in sorted(merged.items()) if w is not None and start_iso <= date <= end_iso
    ]
