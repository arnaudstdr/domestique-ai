"""Provenance numérique : anti-hallucination du coach (promesse du README).

Règle : toute valeur chiffrée « métrique » citée par le LLM (CTL, ATL, TSB,
FTP, TSS, HRV, watts, bpm, %, km, kg) doit exister dans le corpus de vérité du
tour — résultats des tools, contexte système injecté (qui vient du même calcul
que les tools) et message utilisateur. Les valeurs purement lexicales (règles
80/20, zones du prompt système) sont couvertes car le prompt lui-même est dans
le corpus.

Limites documentées (voir ``docs/EVALUATION.md``) : les conversions d'unités
(h ↔ min, kJ ↔ kcal…) et les valeurs recomposées arithmétiquement ne sont pas
reconnues ; les cas dorés évitent ces formulations, et la comparaison se fait
après arrondi à 1 décimale pour absorber virgule/point et arrondis d'affichage.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

# Valeurs libellées (CTL 52.3, CTL est de 52,3, TSB -12.5, FTP de 250…).
_LABELED_RE = re.compile(
    r"\b(?:CTL|ATL|TSB|FTP|TSS|HRV|W/kg)\b\s*"
    r"(?:=\s*|:\s*|est\s+de\s+|est\s+à\s+|de\s+|à\s+)?"
    r"(-?\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
# Valeurs avec unité métier (250 W, 165 bpm, 85 %, 120 km, 70 kg).
_UNIT_RE = re.compile(
    r"(?<![\w.,-])(-?\d+(?:[.,]\d+)?)\s*(?:W\b|bpm\b|%|km\b|kg\b)",
    re.IGNORECASE,
)
# Décimaux nus (52,3 ; 1.05) — les entiers nus sont volontairement ignorés
# (« 2 séances », « 3 sorties » sont du langage, pas des métriques).
_DECIMAL_RE = re.compile(r"(?<![\w.,])(-?\d+[.,]\d+)(?!\d)")

_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def _to_float(raw: str) -> float:
    return float(raw.replace(",", "."))


def extract_metric_values(text: str) -> list[tuple[str, float]]:
    """Extrait les couples (texte cité, valeur) métriques d'une réponse, sans doublon."""
    found: list[tuple[str, float]] = []
    seen: set[str] = set()
    for regex in (_LABELED_RE, _UNIT_RE, _DECIMAL_RE):
        for match in regex.finditer(text):
            raw = match.group(1)
            if raw in seen:
                continue
            seen.add(raw)
            found.append((raw, _to_float(raw)))
    return found


def corpus_numbers(*sources: Iterable[Any]) -> set[float]:
    """Nombres (arrondis à 1 décimale) présents dans les sources de vérité.

    ``sources`` accepte des structures JSON-sérialisables (résultats de tools)
    et des textes (messages système/user).
    """
    numbers: set[float] = set()
    for source in sources:
        if isinstance(source, str):
            text = source
        else:
            text = json.dumps(source, ensure_ascii=False, default=str)
        for raw in _NUMBER_RE.findall(text):
            numbers.add(round(_to_float(raw), 1))
    return numbers


def find_unprovenanced(
    answer: str,
    corpus: set[float],
    *,
    allow: Iterable[float] = (),
) -> list[str]:
    """Liste les valeurs citées par la réponse absentes du corpus autorisé."""
    allowed = {round(value, 1) for value in allow}
    missing: list[str] = []
    for raw, value in extract_metric_values(answer):
        rounded = round(value, 1)
        if rounded in corpus or rounded in allowed:
            continue
        missing.append(raw)
    return missing
