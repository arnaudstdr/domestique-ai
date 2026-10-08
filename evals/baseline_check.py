"""CLI de vérification du baseline d'évaluation (gate CI).

Usage :
    python evals/baseline_check.py --results eval-reports/results.json
    python evals/baseline_check.py --results eval-reports/results.json --update

Codes de sortie : 0 = baseline respecté (ou mise à jour) ; 1 = écart bloquant
(prompt modifié, régression, cas disparu/ajouté) ; 2 = erreur d'entrée.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from evals.lib import prompts  # noqa: E402
from evals.lib.baseline import (  # noqa: E402
    BaselineError,
    build_baseline,
    compare,
)

_DEFAULT_RESULTS = _REPO_ROOT / "eval-reports" / "results.json"
_DEFAULT_BASELINE = Path(__file__).resolve().parent / "baseline.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vérifie le baseline d'évaluation LLM.")
    parser.add_argument("--results", type=Path, default=_DEFAULT_RESULTS)
    parser.add_argument("--baseline", type=Path, default=_DEFAULT_BASELINE)
    parser.add_argument(
        "--update",
        action="store_true",
        help="réécrit la baseline depuis le run courant (après relecture du rapport)",
    )
    args = parser.parse_args(argv)

    try:
        results = json.loads(args.results.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[baseline] résultats illisibles ({args.results}) : {exc}", file=sys.stderr)
        return 2

    prompt_sha = prompts.compute_prompt_sha()
    if args.update:
        args.baseline.write_text(
            json.dumps(build_baseline(results, prompt_sha=prompt_sha), indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        print(f"[baseline] mise à jour : {args.baseline} (prompt_sha={prompt_sha})")
        return 0

    try:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[baseline] baseline illisible ({args.baseline}) : {exc}", file=sys.stderr)
        return 2

    try:
        comparison = compare(results, baseline, prompt_sha=prompt_sha)
    except BaselineError as exc:
        print(f"[baseline] {exc}", file=sys.stderr)
        return 2

    print(f"[baseline] {comparison.summary()}")
    return 0 if comparison.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
