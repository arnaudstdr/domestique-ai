"""
Push d'un plan d'entraînement vers Garmin Connect (API non officielle).

S'appuie sur le paquet ``garminconnect`` (wrapper autour de ``garth``). Construit
le payload JSON attendu par l'endpoint ``/proxy/workout-service/workout`` à
partir de nos dataclasses ``Workout`` / ``WorkoutStep``, et planifie les séances
sur le calendrier Garmin Connect.

Authentification :
- 1ʳᵉ connexion : ``python -m domestique_ai.export.garmin_connect`` (interactif,
  prompt MFA si nécessaire) — persiste un cache token dans
  ``data/.garmin_tokens``.
- Connexions suivantes : silencieuses tant que le token est valide.

⚠️ Endpoints non officiels : peuvent changer sans préavis. Le module isole les
mappings pour faciliter une éventuelle adaptation.
"""

from __future__ import annotations

import contextlib
import getpass
from pathlib import Path
from typing import Any

from domestique_ai.config import (
    get_garmin_credentials,
    get_garmin_token_dir,
)
from domestique_ai.processing.analyzer import _HR_ZONE_BOUNDS, HR_ZONE_KEYS
from domestique_ai.processing.plan_builder import Workout, WorkoutStep

# Bornes %HRR par zone (cohérent avec analyzer + export.fit).
_ZONE_HRR_RANGES: dict[str, tuple[float, float]] = {
    "z1": (0.0, _HR_ZONE_BOUNDS[0]),
    "z2": (_HR_ZONE_BOUNDS[0], _HR_ZONE_BOUNDS[1]),
    "z3": (_HR_ZONE_BOUNDS[1], _HR_ZONE_BOUNDS[2]),
    "z4": (_HR_ZONE_BOUNDS[2], _HR_ZONE_BOUNDS[3]),
    "z5": (_HR_ZONE_BOUNDS[3], 1.0),
}

# Mapping de nos phases internes vers les ``stepType`` Garmin Connect.
_STEP_TYPE: dict[str, dict[str, Any]] = {
    "warmup": {"stepTypeId": 1, "stepTypeKey": "warmup"},
    "active": {"stepTypeId": 3, "stepTypeKey": "interval"},
    "rest": {"stepTypeId": 4, "stepTypeKey": "recovery"},
    "cooldown": {"stepTypeId": 2, "stepTypeKey": "cooldown"},
}

_END_CONDITION_TIME = {"conditionTypeId": 2, "conditionTypeKey": "time"}
_TARGET_HR_ZONE = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"}
_SPORT_CYCLING = {"sportTypeId": 2, "sportTypeKey": "cycling"}


class GarminPushError(RuntimeError):
    """Erreur lors d'un push vers Garmin Connect (auth, payload, réseau)."""


# ---------------------------------------------------------------------------
# Construction du payload
# ---------------------------------------------------------------------------


def _zone_to_bpm_range(zone: str, hr_rest: float, hr_max: float) -> tuple[int, int]:
    """Bornes BPM (Karvonen) pour une zone z1..z5."""
    if zone not in _ZONE_HRR_RANGES:
        zone = "z2"
    low_pct, high_pct = _ZONE_HRR_RANGES[zone]
    hrr = max(1.0, hr_max - hr_rest)
    low = int(round(hr_rest + low_pct * hrr))
    high = int(round(hr_rest + high_pct * hrr))
    return low, max(low + 1, high)


def _zone_to_index(zone: str) -> int:
    """Indice 1..5 pour une zone z1..z5 (zones standard Garmin)."""
    if zone in HR_ZONE_KEYS:
        return HR_ZONE_KEYS.index(zone) + 1
    return 2


def _build_step_payload(
    step: WorkoutStep, order: int, hr_rest: float | None, hr_max: float | None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": _STEP_TYPE.get(step.phase, _STEP_TYPE["active"]),
        "endCondition": _END_CONDITION_TIME,
        "endConditionValue": float(step.duration_sec),
        "targetType": _TARGET_HR_ZONE,
    }
    if hr_rest and hr_max and hr_max > hr_rest:
        low, high = _zone_to_bpm_range(step.zone, hr_rest, hr_max)
        payload["targetValueOne"] = low
        payload["targetValueTwo"] = high
    else:
        payload["zoneNumber"] = _zone_to_index(step.zone)
    return payload


def build_workout_payload(
    workout: Workout, hr_rest: float | None = None, hr_max: float | None = None
) -> dict[str, Any]:
    """Construit le payload JSON Garmin Connect pour une séance."""
    steps = [
        _build_step_payload(s, i + 1, hr_rest, hr_max) for i, s in enumerate(workout.structure)
    ]
    return {
        "workoutName": workout.name[:50],
        "sportType": _SPORT_CYCLING,
        "estimatedDurationInSecs": int(workout.duration_min * 60),
        "workoutSegments": [
            {
                "segmentOrder": 1,
                "sportType": _SPORT_CYCLING,
                "workoutSteps": steps,
            }
        ],
    }


# ---------------------------------------------------------------------------
# Client + auth
# ---------------------------------------------------------------------------


def credentials_present() -> bool:
    """True si email/password sont configurés dans ``.env``."""
    email, password = get_garmin_credentials()
    return bool(email and password)


def token_cache_present() -> bool:
    """True si un cache token est déjà présent (pas besoin de relog interactif)."""
    token_dir = get_garmin_token_dir()
    if not token_dir.exists():
        return False
    return any(token_dir.iterdir())


def _new_client(email: str | None, password: str | None, prompt_mfa: Any = None) -> Any:
    from garminconnect import Garmin

    return Garmin(email=email, password=password, prompt_mfa=prompt_mfa)


def get_client(token_dir: Path | None = None) -> Any:
    """Retourne un client Garmin authentifié.

    Lit `.env` pour les credentials et tente de réutiliser le cache token. Si
    le cache est absent ou invalide ET qu'aucun MFA n'est attendu, fait un login
    complet. Si le compte demande un MFA, lève ``GarminPushError`` pour forcer
    le passage par ``login_interactive()``.
    """
    email, password = get_garmin_credentials()
    cache = Path(token_dir) if token_dir else get_garmin_token_dir()

    client = _new_client(email, password)
    try:
        # Si le cache existe, login() le charge et fait un refresh DI silencieux.
        # Sinon, login() utilise email/password et persiste le résultat dans le
        # tokenstore.
        cache.mkdir(parents=True, exist_ok=True)
        client.login(tokenstore=str(cache))
        return client
    except Exception as exc:  # noqa: BLE001 — on remap toutes les erreurs auth
        raise GarminPushError(
            f"Échec d'authentification Garmin Connect : {exc}. "
            f"Lancer `python -m domestique_ai.export.garmin_connect` pour "
            f"initialiser la connexion (MFA inclus)."
        ) from exc


# ---------------------------------------------------------------------------
# Push
# ---------------------------------------------------------------------------


def push_workout(
    client: Any, workout: Workout, hr_rest: float | None = None, hr_max: float | None = None
) -> int:
    """Upload une séance et retourne son ``workoutId`` Garmin Connect."""
    payload = build_workout_payload(workout, hr_rest=hr_rest, hr_max=hr_max)
    try:
        response = client.upload_workout(payload)
    except Exception as exc:  # noqa: BLE001
        raise GarminPushError(f"upload_workout a échoué : {exc}") from exc
    workout_id = response.get("workoutId") if isinstance(response, dict) else None
    if not workout_id:
        raise GarminPushError(f"Réponse inattendue de Garmin : {response!r}")
    return int(workout_id)


def push_plan(
    plan: list[Workout],
    *,
    schedule: bool = True,
    hr_rest: float | None = None,
    hr_max: float | None = None,
    client: Any = None,
    progress: Any = None,
) -> list[dict[str, Any]]:
    """Upload chaque séance puis (optionnel) la planifie sur le calendrier.

    Si ``client`` n'est pas fourni, en construit un via ``get_client()``. Le
    callable ``progress(i, total, workout)`` est appelé avant chaque upload pour
    nourrir un éventuel ``st.progress``.

    Retourne la liste des résultats (ordre du plan) :
        ``{"workout", "date", "workout_id", "scheduled", "url" | "error"}``
    """
    if client is None:
        client = get_client()

    results: list[dict[str, Any]] = []
    total = len(plan)
    for idx, workout in enumerate(plan):
        if progress is not None:
            # L'UI ne doit pas casser le push — on encaisse silencieusement.
            with contextlib.suppress(Exception):
                progress(idx, total, workout)
        try:
            workout_id = push_workout(client, workout, hr_rest=hr_rest, hr_max=hr_max)
        except GarminPushError as exc:
            results.append(
                {
                    "workout": workout.name,
                    "date": workout.date,
                    "workout_id": None,
                    "scheduled": False,
                    "error": str(exc),
                }
            )
            continue

        scheduled = False
        scheduling_error: str | None = None
        if schedule:
            try:
                client.schedule_workout(workout_id, workout.date)
                scheduled = True
            except Exception as exc:  # noqa: BLE001
                scheduling_error = str(exc)

        entry: dict[str, Any] = {
            "workout": workout.name,
            "date": workout.date,
            "workout_id": workout_id,
            "scheduled": scheduled,
            "url": f"https://connect.garmin.com/modern/workout/{workout_id}",
        }
        if scheduling_error:
            entry["error"] = f"schedule failed: {scheduling_error}"
        results.append(entry)

    return results


# ---------------------------------------------------------------------------
# CLI : 1ʳᵉ connexion (MFA inclus)
# ---------------------------------------------------------------------------


def login_interactive(token_dir: Path | None = None) -> Path:
    """Login interactif (CLI) — prompts pour MFA si nécessaire.

    Retourne le chemin du dossier de cache où le token a été persisté.
    """
    email, password = get_garmin_credentials()
    if not email:
        email = input("Email Garmin Connect : ").strip()
    if not password:
        password = getpass.getpass("Mot de passe : ").strip()
    if not email or not password:
        raise GarminPushError("Email et mot de passe requis.")

    cache = Path(token_dir) if token_dir else get_garmin_token_dir()
    cache.mkdir(parents=True, exist_ok=True)

    def _prompt_mfa() -> str:
        return input("Code MFA Garmin (reçu par email/SMS) : ").strip()

    client = _new_client(email, password, prompt_mfa=_prompt_mfa)
    try:
        client.login(tokenstore=str(cache))
    except Exception as exc:  # noqa: BLE001
        raise GarminPushError(f"Login Garmin échoué : {exc}") from exc

    return cache


def _main() -> None:  # pragma: no cover — entrée CLI manuelle
    try:
        path = login_interactive()
    except GarminPushError as exc:
        print(f"❌ {exc}")
        raise SystemExit(1) from exc
    print(f"✅ Connexion réussie. Token persisté dans : {path}")
    print("Tu peux maintenant pousser tes plans depuis l'onglet « 📋 Plan » du dashboard.")


if __name__ == "__main__":  # pragma: no cover
    _main()


__all__ = [
    "GarminPushError",
    "build_workout_payload",
    "credentials_present",
    "get_client",
    "login_interactive",
    "push_plan",
    "push_workout",
    "token_cache_present",
]
