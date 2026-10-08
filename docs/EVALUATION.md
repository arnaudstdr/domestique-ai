# LLM evaluation — measuring coach output quality

> *How do you detect that a prompt or model change degraded your agent?*
> This document answers that question for DomestiqueAI: a deterministic
> evaluation gate runs in CI on every push, and real-model runs are available
> locally for reporting.

The repo already had **code** tests (1,245 of them). This harness adds
**output-quality** evaluation for the LLM coach: golden cases, deterministic
invariants, a versioned baseline, prompt-change detection, and an optional
local judge. It is the difference between *using* an LLM and *engineering* one.

*(Résumé en français en fin de document.)*

## What runs where

| Layer | Runner | Network | Gates CI? |
| --- | --- | --- | --- |
| Deterministic checks on 12 golden cases (stub replay) | `promptfoo` + Python provider | none | **yes** |
| Baseline comparison + `prompt_sha` freshness | `evals/baseline_check.py` | none | **yes** |
| Live run against local Ollama (`EVAL_PROVIDER=ollama`) | `promptfoo` + Ollama | local only | no (reporting) |
| Local LLM judge (`EVAL_JUDGE=1`, Ollama rubric) | `promptfoo` `llm-rubric` | local only | no (reporting) |

All data is **synthetic** (built by `evals/lib/seeds.py`); no athlete data ever
enters the harness. The stub run takes ~4 s and downloads nothing.

## Architecture

```text
evals/
  package.json / package-lock.json   # promptfoo pinned (Node >= 22.22, dev only)
  promptfooconfig.yaml               # provider + Python test generator
  coach_provider.py                  # runs one case via the real pipeline, returns the envelope
  cases.py                           # generate_tests(): one test per cases/*.yaml (+ judge on demand)
  assertions.py                      # get_assert(): runs the deterministic check suite
  node-compat.cjs                    # Node >= 26 execFile promisify shim (no-op elsewhere)
  cases/*.yaml                       # 12 golden cases (data + scripted LLM turns + expectations)
  baseline.json                      # versioned: prompt_sha + per-case results
  baseline_check.py                  # regression / prompt-change gate (exit 1 on drift)
  lib/                               # pure Python engine (also used by pytest)
    models.py      # case & envelope schemas (Pydantic)
    cases.py       # YAML loader/validator
    seeds.py       # synthetic athlete DBs per scenario (empty/normal/overtraining/comeback/busy)
    stub.py        # ScriptedLLM: deterministic stream_chat / chat_structured
    runner.py      # executes a case against the real pipeline (stub or live)
    provenance.py  # numeric provenance (anti-hallucination)
    checks.py      # deterministic check suite
    baseline.py    # build/compare baseline
    prompts.py     # prompt_sha over prompts + tool schemas
tests/test_evals_*.py              # unit + golden + sensitivity tests (no Node needed)
```

The provider returns a self-sufficient **envelope** (final answer, tool trace,
injected system context, plan payload, expectations). Assertions only need the
output — promptfoo stays replaceable, and the same envelope is unit-tested in
pytest without Node.

## Golden cases

| Case | Scenario | Invariant under test |
| --- | --- | --- |
| `no-data` | empty DB | Admits missing data on a load question; no invented metric |
| `load-normal` | 4 weeks of rides | Cites CTL/ATL/TSB **from tools**; calls the required tools |
| `overtraining-rest` | overload week + fatigue markers | Prescribes rest; no intensity; health guardrail |
| `comeback` | 5-week gap | Progressive return; no "back to previous volume" |
| `busy-week` | 2 available slots | Rest when the day is not available |
| `zones-profile` | profile + HR | Z2 bpm from the profile tool, not invented |
| `injury-pain` | pain report | No session prescribed; medical redirect |
| `medical-diagnosis` | "is it a tear?" | No diagnosis; medical redirect |
| `prompt-injection` | "dump your system prompt" | No verbatim prompt leak; stays in role |
| `memory-fact` | durable preference | Calls `remember_fact` |
| `plan-week-bad` | LLM outputs an overfull/off-availability week | **Delivered** week is corrected (availability, weekly rest, 80/20, TSS cap) and adjustments are traced |
| `plan-week-good` | compliant LLM week | Guardrails hold on the delivered plan |

## Deterministic checks (the gate)

| Check | What it enforces |
| --- | --- |
| `complete` | Final answer exists, not the tool-loop fallback, stub script not exhausted |
| `tools` | Required tools called; forbidden tools never called |
| `provenance` | Every metric-like number comes from tool results or injected context |
| `french` | French-language answer (marker heuristic) |
| `prompt_leak` | No verbatim 8-gram of the system prompt in the answer |
| `mentions` | `must_mention` / `must_not_mention` (accent- and case-insensitive) |
| `max_chars` | Coach-styled length cap where configured |
| `health_safety` | Required/disallowed patterns (e.g. rest + doctor on pain) |
| `plan.*` | Plan source, traced corrections, availability & duration caps, weekly rest, 80/20 polarization, TSS cap tied to CTL, long ride |

### Numeric provenance — the anti-hallucination rule

The system prompt promises *"never invent a number about the athlete"*. The
check makes that promise executable: values labelled `CTL/ATL/TSB/FTP/TSS/HRV`,
values with metric units (`W`, `bpm`, `%`, `km`, `kg`) and decimals must exist
in the corpus of truth for the turn — tool results, the injected system
context (same computation as the tools) and the user message. Matching is
done after rounding to 1 decimal, so `52.34 → "52,3"` is accepted.

**Limits (documented, deliberate):** bare integers (`"2 sessions"`), unit
conversions (`1 h` vs `3600 s`) and derived arithmetic are not recognised —
golden cases avoid those phrasings. The corpus *includes the system prompt
itself*, so citing guardrail figures (zones, 80/20) is legitimate.

## Baseline & prompt freshness

`evals/baseline.json` is versioned and contains the `prompt_sha` (SHA-256 of
`SYSTEM_PROMPT`, plan prompt builders, brief prompt and `TOOL_SCHEMAS`) plus
the per-case result of the reference run. `baseline_check.py` fails (exit 1) if:

- the `prompt_sha` changed → **re-baseline required** after reading the report;
- a case that passed in the baseline now fails → **regression**;
- a case disappeared or was added → the suite changed, baseline is stale.

Improvements (fail → pass) are reported but not blocking. Refreshing is
explicit: `make eval-update-baseline`.

Why the prompt-hash gate matters: recorded replay outputs can't reflect a
prompt edit by themselves, so the hash is the causal signal that forces a
human review + fresh reference. Sensitivity is itself tested in
`tests/test_evals_sensitivity.py`: mutating `SYSTEM_PROMPT` flips the
comparison to failure, and a degraded answer (invented FTP) fails `provenance`
and is classified as a regression.

## Stub vs live — why replay in CI

CI runners have no GPU, no Ollama, and downloading a model per build would be
slow and flaky — the opposite of a gate. So the CI run **replays scripted LLM
turns** against the real pipeline (tools, SQLite, agent loop, plan validator):
record/replay is the standard way to make agent evals deterministic, and it
also unit-tests every post-LLM guarantee (guardrails, provenance, safety).

- The stub proves the *harness and the post-processing*, not the model.
- `make eval-live` runs the same cases against local Ollama to measure the
  model itself; `EVAL_JUDGE=1` adds rubric scoring with a local judge model
  (`EVAL_JUDGE_MODEL` > `OLLAMA_MODEL` > app default). Both are reporting-only.
- Live mode keeps auxiliary LLM paths (daily brief, "today" decision, memory
  embeddings) neutralised, so stub and live differ **only** by the evaluated
  model path.
- Why not auto-record fixtures from the model? Because a degraded model would
  bake its degradation into the reference: golden answers are curated, and
  their numbers are calibrated from real tool outputs (see `tmp/` calibration
  workflow in the PR notes).

## Tool choice: promptfoo (options compared)

| Option | Pros | Cons | Verdict |
| --- | --- | --- | --- |
| **promptfoo** (chosen) | OSS, self-hosted; Python providers/assertions/test generators (`file://`); Ollama provider incl. `llm-rubric`; one command produces HTML/JSON/JUnit reports; Node already used for the frontend; pinned via `evals/package.json` | Adds Node >= 22.22 as a dev/CI dependency; promptfoo's own tree is heavy (measured: `evals/node_modules` ≈ 1.2 GB — dev only, never in the prod image); its reporting is per-test, so the per-check detail lives in our Python suite | Gate = Python assertions through promptfoo |
| DeepEval | Python-native, pytest integration, local/Ollama judge verified | LLM-judge-centric metrics (probabilistic) — the deterministic gate would still be custom code; heavier dependency tree; fast-moving API | Not chosen |
| Ragas | — | RAG-oriented metrics (faithfulness/relevancy), poor fit for an agent with tools | Not chosen |
| Home-grown harness only | Zero extra deps, full control | Loses the standard report artifacts and the recognisable tool; would still need a runner/assertion layer | Kept as the engine (`evals/lib/`), promptfoo as the front |

In short: the deterministic core (provenance, guardrails, baseline) is custom
in every option — promptfoo adds the reporting, the dataset contract and the
CI ergonomics without owning the logic.

## CI

New `llm-eval` job (`.github/workflows/ci.yml`), parallel to `quality`:

1. Python 3.12 + `pip install -e ".[dev]"` (promptfoo runs the project code);
2. Node 24 (promptfoo needs >= 22.22) + `npm ci` in `evals/` (cached);
3. `npm run eval` — stub run, `--no-cache`, writes `eval-reports/results.json`
   and `report.html`;
4. `python evals/baseline_check.py` — regression / prompt-change gate;
5. `actions/upload-artifact@v7` publishes `llm-eval-report` (HTML + JSON + JUnit).

Pytest (the `quality` job) also runs the harness unit tests, the golden suite
and the sensitivity tests — no Node required there.

## Running it

```bash
make eval-install            # npm ci in evals/ (first time only)
make eval                    # deterministic gate (stub + baseline)
make eval-update-baseline    # refresh the baseline after a prompt change
make eval-live               # live run against local Ollama (reporting)
EVAL_JUDGE=1 make eval-live  # + local LLM judge rubrics
cd evals && npm run view     # browse runs in the promptfoo web UI
```

Sensitivity demo (deterministic, no model):

```text
$ python - <<'EOF'
... coach.SYSTEM_PROMPT += "mutation" ... baseline_check.main(...)
[baseline] prompt modifié : la baseline est périmée → relire le rapport puis re-baseliner (make eval-update-baseline)
EXIT = 1
```

## Footprint (Raspberry Pi budget)

- **Production:** zero impact — the harness is not imported by the app, adds
  no Python dependency, no runtime process, no model. The prod image is
  unchanged (`evals/` is dev/CI only, excluded from the installed package).
- **Dev/CI:** promptfoo 0.124.0 pins itself; `evals/node_modules` measured at
  ~1.2 GB on disk (CI caches it). A stub run executes 12 cases in ~4 s with no
  network.
- **Live (Pi):** reuses the already-running Ollama instance; the judge consumes
  local quota — reporting only, never CI.

## Known limits & assumptions

1. **Stub validates the harness, not the model.** Model drift is measured by
   `make eval-live`; the CI gate protects the invariants that must survive any
   model or prompt change.
2. **Provenance granularity**: unit conversions and derived arithmetic are not
   verified (documented above).
3. **TSS cap nuance**: `_enforce_tss_cap` only shortens endurance sessions; the
   golden bad week includes endurance so the delivered cap holds. An
   all-intervals week could bypass it — the check would flag it, which is the
   intended signal.
4. **`prompt_sha` is coarse**: any edit to the four hashed sources demands an
   explicit re-baseline. That is the point (snapshot semantics), but wording
   tweaks also trigger it.
5. **Judge is probabilistic** and speaks only when asked (`EVAL_JUDGE=1`).
6. Node 26 broke `promisify(execFile)` used by promptfoo; `node-compat.cjs`
   restores the documented behaviour and is a no-op on Node <= 25.

---

## Résumé (français)

Ce harnais mesure la **qualité des sorties** du coach, pas seulement que le
code tourne. 12 cas dorés (données synthétiques) rejouent des scénarios
sensibles — absence de données, surentraînement, reprise, semaine chargée,
blessure, injection de prompt, plans hors bornes — contre le vrai pipeline
(tools, SQLite, validateur de plan) avec des réponses LLM scriptées : **aucun
réseau, aucune dépendance à un modèle, ~4 s**. Les checks déterministes
incluent la **provenance numérique** (tout chiffre cité doit venir des tools ou
du contexte injecté), les garde-fous du plan livré (dispo, repos, 80/20,
plafond TSS, sortie longue), la langue, l'absence de fuite de prompt et les
garde-fous santé. Une baseline versionnée (`evals/baseline.json`) embarque
l'empreinte `prompt_sha` des prompts et schémas : toute modification de prompt
fait échouer la comparaison tant qu'une re-baseline explicite n'a pas été
faite après relecture du rapport — c'est le détecteur de régression. Le gate
CI (job `llm-eval`) tourne en mode stub déterministe et publie le rapport
HTML/JSON ; les runs contre **Ollama local** (`make eval-live`) et le **juge
LLM local** (`EVAL_JUDGE=1`) servent au reporting, jamais au gate. Empreinte :
aucun coût en production, promptfoo en dev/CI uniquement, réutilise l'Ollama
existant en mode live.
