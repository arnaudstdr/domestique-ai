# AGENTS.md — `evals/`

Harnais d'évaluation des sorties du coach LLM (promptfoo + moteur Python).
Documentation de référence : `docs/EVALUATION.md`. Guide racine : `AGENTS.md`.

## Invariants

- **Gate CI déterministe** : seul le mode stub (réponses LLM scriptées, aucun
  réseau, aucun Ollama) fait échouer la CI. Le mode live (`EVAL_PROVIDER=ollama`)
  et le juge LLM (`EVAL_JUDGE=1`) sont du **reporting**, jamais des gates.
- **1 cas = 1 YAML** dans `cases/` : le YAML est la source de vérité (scénario,
  tours scriptés, attentes). Les valeurs chiffrées des réponses dorées doivent
  exister dans les sorties des tools — les calibrer via un run avant de commit.
- **Baseline versionnée** (`baseline.json`) : toute modification de
  `coach.SYSTEM_PROMPT`, des prompts du générateur de plan, du prompt du brief
  ou de `TOOL_SCHEMAS` change `prompt_sha` → le gate exige
  `make eval-update-baseline` **après relecture du rapport**. Ne jamais
  régénérer la baseline sans lire le diff.
- **Moteur pur dans `lib/`** : testable sans Node (`tests/test_evals_*.py`).
  Provider, assertions et générateur ne font que brancher promptfoo sur ce
  moteur — garder cette séparation.
- **Node ≥ 22.22** requis par promptfoo (pinné dans `package.json`, dev/CI
  uniquement, jamais dans l'image prod). `node-compat.cjs` restaure
  `promisify(execFile)` cassé par Node ≥ 26 et ne fait rien sur Node ≤ 25 ; il
  est requis dans les scripts npm.

## Commandes

```bash
make eval                 # gate : promptfoo stub + baseline_check (rapide, hors ligne)
make eval-update-baseline # re-baseline explicite après un changement de prompt
make eval-live            # run live Ollama (local requis) — reporting
EVAL_JUDGE=1 make eval-live  # + rubriques du juge LLM local (EVAL_JUDGE_MODEL)
cd evals && npm run view  # UI promptfoo des runs locaux
```

## Pièges connus

- Ne pas modifier `cases/*.yaml` sans re-passer `make eval` et relire le
  rapport ; les tests de sensibilité vivent dans `tests/test_evals_sensitivity.py`.
- Après un changement de seeds, re-calibrer les réponses dorées avec
  `.venv/bin/python evals/calibrate.py <case-id>` (affiche les sorties réelles
  des tools — la provenance numérique exige qu'elles correspondent).
- `evals/node_modules` pèse ~1.2 Go (dev/CI seulement, ignoré par git).
- Le provider recharge les cas par `case_id` : ne pas dupliquer un id (le
  loader refuse les doublons).
