"""Outil de calibration des cas dorés : affiche les sorties réelles des tools.

Les réponses scriptées d'un cas doivent citer des valeurs qui existent dans les
résultats des tools (la provenance numérique l'exige). Pour calibrer ou
re-calibrer un cas après un changement de seeds :

    .venv/bin/python evals/calibrate.py                 # tous les cas
    .venv/bin/python evals/calibrate.py load-normal     # un ou plusieurs cas

Le script exécute les cas en mode stub (aucun réseau) et imprime, pour chaque
cas, la trace des tools ; pour un cas plan, les semaines livrées et les
ajustements du validateur. Il ne modifie rien.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from evals.lib.cases import load_cases  # noqa: E402
from evals.lib.runner import run_case  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    wanted = set(argv or [])
    cases = [case for case in load_cases() if not wanted or case.id in wanted]
    if not cases:
        print(f"aucun cas ne correspond à {sorted(wanted)}", file=sys.stderr)
        return 2
    for case in cases:
        with tempfile.TemporaryDirectory() as tmp:
            envelope = run_case(case, root=Path(tmp) / case.id)
        print(f"===== {case.id} ({case.kind}) =====")
        if case.kind == "plan":
            for week in (envelope.plan or {}).get("weeks", []):
                print(f"  week {week['week_index']} source={week['source']}")
                print(f"  adjustments: {json.dumps(week['adjustments'], ensure_ascii=False)}")
                print(
                    "  workouts: " + json.dumps(week["workouts"], ensure_ascii=False, default=str)
                )
        else:
            for entry in envelope.tool_trace:
                print(f"  tool={entry['name']} args={entry['arguments']}")
                print(f"    result={json.dumps(entry['result'], ensure_ascii=False, default=str)}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
