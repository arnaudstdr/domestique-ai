<div align="center">

<img src="frontend/public/icon-192.png" alt="DomestiqueAI" width="110" height="110" />

# DomestiqueAI

### A self-hosted, privacy-first training companion for cyclists — powered by a local LLM coach that *never makes up a number.*

It ingests your rides and recovery data, computes the same training-load metrics the
pros use (CTL / ATL / TSB, hr-TSS, HR zones), flags overtraining before you feel it,
lets you *talk* to a coach that grounds every claim in your real data, and
**rewrites your plan week after week** as your body responds.

<br/>

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![React](https://img.shields.io/badge/React_18-20232A?logo=react&logoColor=61DAFB)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?logo=typescript&logoColor=white)
![Ollama](https://img.shields.io/badge/Ollama-local_LLM-blueviolet?logo=ollama&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)
<br/>
![CI](https://github.com/arnaudstdr/domestique-ai/actions/workflows/ci.yml/badge.svg)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
![Tests](https://img.shields.io/badge/tests-674-success)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

<br/>

<table>
  <tr>
    <td><img src="docs/screenshots/dashboard.jpg" alt="Dashboard — CTL/ATL/TSB and training-load curve" /></td>
    <td><img src="docs/screenshots/coach.jpg" alt="LLM coach — grounded conversation citing real numbers" /></td>
    <td><img src="docs/screenshots/activity.jpg" alt="Activity detail — GPS map and rich metrics" /></td>
  </tr>
  <tr>
    <td align="center"><sub><b>Dashboard</b> — fitness state &amp; alerts</sub></td>
    <td align="center"><sub><b>Coach</b> — grounded, no hallucinated numbers</sub></td>
    <td align="center"><sub><b>Activity</b> — map, HR / power / elevation</sub></td>
  </tr>
</table>

</div>

---

## The problem it solves

Tools like TrainingPeaks are powerful but **expensive, closed, and they own your data**.
Generic AI chatbots will happily *invent* a TSB value or a heart-rate zone — useless,
sometimes dangerous, for training decisions.

**DomestiqueAI** is the opposite bet:

- 🔒 **You own everything.** SQLite on your own hardware. No cloud, no subscription, no data broker.
- 🧠 **A coach that can't lie about numbers.** The LLM is *forced* to call a Python tool
  before stating any metric. The math lives in tested code; the model only explains it.
- 🏠 **Runs on a Raspberry Pi.** The whole stack — API, PWA, local LLM — self-hosts on a Pi 5,
  reachable from anywhere over Tailscale.

> **In one line:** an end-to-end product — data pipeline, sports-science engine, agentic LLM,
> PWA and self-hosted deployment — built and shipped solo.

<p align="center">
  <img src="docs/screenshots/daily-brief.jpg" alt="Daily brief — an LLM-written summary plus physiology-based alerts" width="340" />
  <br/>
  <sub>The proactive daily brief: an LLM-written summary on top of physiology-based overtraining alerts.</sub>
</p>

---

## Highlights

<table>
<tr>
<td width="50%" valign="top">

### 🧮 A real sports-science engine
Not a wrapper around an API. `hr-TSS` is a **Banister exponential TRIMP**,
normalized so 1 h at threshold = exactly **100 points** — making it
interchangeable with power-based TSS. CTL / ATL / TSB are EMAs computed over
*every* calendar day, rest days included.

</td>
<td width="50%" valign="top">

### 🤖 An agentic coach with guardrails
The LLM runs a **tool-calling loop** over 13 typed tools. A golden rule in the
system prompt forbids any unsourced figure. `thinking` mode is toggled per turn
to balance reliability and latency. Responses **stream over SSE**, token by token.

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🩺 Overtraining detection
Four automatic indicators grounded in the physiology literature
(**Foster 2001, Banister**): chronic TSB, Monotony, Strain, weekly volume jump —
plus a **health module** (HRV, resting HR, sleep + stages, SpO₂, skin temp)
auto-imported from the **Google Health API** (Fitbit / Pixel Watch), with a
14-day rolling baseline, drift alerts and local sleep / readiness scores.

</td>
<td width="50%" valign="top">

### 🛡️ LLM output you can trust
Plan generation is **two-stage**: the LLM only picks high-level choices, then
*six deterministic guardrails* enforce availability, weekly rest, 80/20
polarization, a CTL-based TSS ceiling, intensity cadence per goal type and the
long ride. On a comeback, intensity is capped while fitness rebuilds. If the
model fails, a deterministic builder takes over — week by week.

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 📲 Installable PWA
React 18 + Vite + Tailwind, offline-aware service worker (NetworkFirst on `/api/`).
Interactive GPS maps (react-leaflet), live charts (recharts), `.ZIP` (FIT) / `.ICS`
export, and a **webcal subscription feed** that lands every session in Apple /
Google Calendar and updates itself after each weekly review.

</td>
<td width="50%" valign="top">

### 🔁 An idempotent, resilient pipeline
Incremental Garmin Connect sync (sole source, legacy rides de-duplicated) derived
from `MAX(date)`, enriched fields + activity streams + weather, soft schema
migrations, and a background scheduler with anti-overlap claims, **Pushover**
notifications, **Sentry** error tracking and a **Healthchecks.io dead-man's-switch**
so a crash on the Pi notifies *you*.

</td>
</tr>
<tr>
<td colspan="2" valign="top">

### 🔐 Real auth you actually control
Email + password (Argon2id) with **mandatory TOTP two-factor** and one-time
recovery codes. Opaque, HMAC-hashed sessions per account, `coach` / `athlete`
roles, and strict per-athlete data isolation (one SQLite file each). A coach
onboards athletes with a one-time invite link; everyone manages their own
credentials and 2FA. The upgrade is an **additive migration** — existing
databases, athletes and sessions keep working.

</td>
</tr>
</table>

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| **Backend** | FastAPI · Pydantic v2 · APScheduler · `sse-starlette` | Async, typed, one router per domain (13 of them) |
| **Security** | Argon2id · TOTP (`pyotp`) + recovery codes · HMAC-hashed opaque sessions | Per-account login, mandatory 2FA, coach/athlete roles |
| **Frontend** | React 18 · Vite · TypeScript · Tailwind · recharts · react-leaflet | Installable PWA, manual service worker |
| **LLM** | Ollama (local) · agentic tool-calling loop | Privacy, zero API cost, no hallucinated metrics |
| **Data** | SQLite (single source of truth) | Idempotent on external activity ids, soft migrations |
| **Integrations** | Garmin Connect · Google Health · Pushover · Healthchecks.io · Sentry | Real third-party APIs, real failure handling |
| **Quality** | pytest (630+ tests) · Ruff · Semgrep · GitHub Actions CI | Tested, linted, green on every push |
| **Deploy** | Docker · Raspberry Pi 5 · Tailscale Funnel | Self-hosted, reachable anywhere |

### Architecture

A 4-layer pipeline, each isolated in its own sub-package:

```text
config.py  ──►  ingestion/  ──►  processing/  ──►  api/ + frontend/  (PWA)
   │                │                │                    │
   │                │                │                    └─ FastAPI + React
   └─ .env / paths  └─ Garmin + DB   └─ TSS, CTL/ATL/TSB   ▲
                              │                            │
                              └──────────►  llm/  (agentic coach, SSE)
```

```text
domestique_ai/
├── config.py          # data paths, FTP, HR profile, secrets — single source via .env
├── platform_db.py     # multi-tenant identity — accounts, sessions, invites, recovery codes
├── security.py        # Argon2id password hashing, TOTP, signed 2FA challenge
├── auth_cli.py        # bootstrap / lockout-recovery CLI (set credentials, enroll 2FA)
├── ingestion/         # Garmin Connect sync + SQLite persistence (schema, migrations)
├── processing/        # TSS / hr-TSS, CTL/ATL/TSB, HR zones, overtraining, trends, plans
├── llm/               # Ollama wrapper, tools, agentic coach loop, plan generator
├── api/               # FastAPI app — one router per domain, Bearer/2FA middleware (+ SSE)
└── export/            # GPX / FIT files + Garmin Connect push
frontend/              # React 18 + Vite + TypeScript + Tailwind PWA
```

---

## Engineering decisions

The choices below are where most of the design effort went — they're the part
that's worth a conversation.

<details>
<summary><b>Why a local LLM instead of the OpenAI / Anthropic API?</b></summary>

<br/>

Three reasons, in priority order: **privacy** (training data never leaves the user's
hardware), **cost** (zero per-token billing on a tool that runs daily), and **control**
(I can toggle `thinking` mode per turn and shape the tool loop without rate limits).
The trade-off is model capability — mitigated by the guardrail architecture below:
the model never *computes*, it only *explains* numbers produced by tested Python.

</details>

<details>
<summary><b>Why force the LLM through tools instead of giving it the data in the prompt?</b></summary>

<br/>

A model handed a table of metrics will still paraphrase, round, or invent values under
pressure. By exposing **13 typed tools** and a system prompt that forbids any quantitative
claim without a tool call, the source of truth stays in code. The tools return
JSON-serializable dicts computed by the same functions that power the dashboard — so the
chat and the charts can never disagree.

<p align="center">
  <img src="docs/screenshots/coach-tools.jpg" alt="Raw tool-call output the coach reads from — get_training_load_state returns the real CTL/ATL/TSB" width="320" />
  <br/>
  <sub>The coach reads CTL/ATL/TSB straight from a tool call — it never types a number itself.</sub>
</p>

</details>

<details>
<summary><b>Why normalize hr-TSS to 100 points at threshold?</b></summary>

<br/>

Without a reliable FTP, power-based TSS isn't available. A raw TRIMP score isn't
comparable to TSS, which breaks CTL/ATL/TSB interpretation. Anchoring the Banister TRIMP
so that **1 h at 88 % HRR = 100 points** makes the HR-derived score *interchangeable* with
a power score on the same scale — the rest of the engine doesn't need to know which mode
produced the load.

</details>

<details>
<summary><b>Why wrap LLM plan generation in deterministic validators?</b></summary>

<br/>

Free-form LLM output can produce dangerous training (e.g. 20 min of Z5 back-to-back).
Instead, the LLM only picks high-level choices (`kind`, `duration`, `notes`); the code
rebuilds the structure and then runs **six ordered guardrails**: availability, weekly
rest cap, 80/20 polarization, a CTL-based TSS ceiling, intensity cadence per goal type,
and the dedicated long ride. Each correction is surfaced in the UI as an "adjusted" badge.
If validation fails twice, that week falls back to a fully deterministic builder — the
others can stay LLM-generated.

</details>

<details>
<summary><b>Why does the plan rewrite itself every week?</b></summary>

<br/>

A fixed 4-week block is wrong the moment real life — or real fatigue — intervenes. Two
loops keep the plan honest, both with a deterministic fallback so the LLM only *writes the
rationale*, never decides out of bounds:

- **Daily morning check** (08:00, after a fresh Google Health + Garmin pre-sync): if
  readiness is low or sleep is short, the day's session becomes `go` / `adjust` / `rest`,
  and the change is persisted and shown on the Plan as "coach rest".
- **Weekly review** (Sunday 18:00): a compliance report (planned vs. done, morning trends,
  overtraining alerts, TSB) drives a `reduce` / `maintain` / `progress` decision, then the
  coach **re-composes only the upcoming week** on top of real fitness — the rest of the
  plan stays and gets re-evaluated next week. Each re-plan is saved as a new version.

When the athlete is deconditioned (`athlete_state.py`), intensity is capped by a graduated
ceiling: base-only first, then tempo, then full — ramp length scaled to the athlete's level.

</details>

<details>
<summary><b>Why SQLite and not Postgres?</b></summary>

<br/>

The workload is single-user, read-heavy, and self-hosted on a Pi. SQLite means **zero
ops, one file to back up, and trivial idempotency** via `UNIQUE` constraints on the
external activity ids. Schema evolution is handled with soft migrations (`_ensure_column`)
so existing databases upgrade in place. Postgres would add operational weight for no
benefit at this scale.

</details>

<details>
<summary><b>How does multi-user auth work without ever locking anyone out?</b></summary>

<br/>

The app is self-hosted and multi-tenant: a `coach` owns the roster, each `athlete`
gets **its own SQLite file** under `data/athletes/<public_id>/`, and identity lives in
a separate `data/platform.db` (accounts, sessions, invites, recovery codes).

- **Credentials**: email + password hashed with **Argon2id**, then **mandatory TOTP
  2FA** with single-use recovery codes. Sessions are opaque tokens, stored only as
  HMAC digests — never in plaintext.
- **Coach → athlete flow**: the coach generates a one-time invite link; the athlete
  sets email + password and enrols 2FA themselves. A coach can read an athlete's data
  (read-only) but never touches their credentials.
- **No lockout**: the legacy `DOMESTIQUE_AI_API_TOKEN` stays valid as a break-glass,
  a local CLI (`auth_cli`) can set credentials or reset 2FA from the Pi, and
  pre-existing sessions are grandfathered so a deploy never disconnects everyone at
  once.
- **No data loss**: the schema change is additive (`_ensure_column`); activity
  databases are untouched, and the upgrade path is covered by a test that migrates a
  legacy database in place.

</details>

---

## Quality &amp; rigor

- **674 tests** across **46 modules** — load math, HR zones, Garmin ingestion (mocked,
  no network), Google Health, source de-duplication, ICS/FIT export, webcal feed,
  conversations, coach tools, health metrics, overtraining, trends, plan generation, its
  validators, the adaptive daily/weekly decision loops, and the full auth stack
  (Argon2id, TOTP, recovery codes, session middleware, legacy-DB migration).
- **Ruff** (`E, F, I, UP, B, SIM`), **Semgrep** scans and **GitHub Actions CI** green on every push.
- Tests isolate state with `tmp_path` fixtures — **no shared DB, no flakiness**.

```bash
pytest          # run the suite
ruff check .    # lint
```

---

## Run it yourself

<details>
<summary><b>Setup, OAuth &amp; usage (click to expand)</b></summary>

<br/>

### Install

```bash
git clone https://github.com/arnaudstdr/domestique-ai.git
cd domestique-ai
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill in the values
```

### Authentication (accounts &amp; 2FA)

The API is protected by per-account **email + password** with **mandatory TOTP 2FA**.
Set `DOMESTIQUE_AI_API_TOKEN` (break-glass) and, recommended, a fixed
`DOMESTIQUE_AI_SESSION_SECRET` in `.env`, then create the owner account once from the host:

```bash
python -m domestique_ai.auth_cli set-credentials --email you@example.com
python -m domestique_ai.auth_cli enroll-totp   # prints a QR code + recovery codes
```

A coach then invites athletes from the **Roster** page: each opens a one-time link, sets
their own email + password and enrols 2FA. To recover a locked-out account, use
`auth_cli reset-2fa` (or set new credentials). See [DEPLOY.md](DEPLOY.md) for the
Raspberry Pi procedure, including upgrading an existing deployment without data loss.

### Garmin Connect (activity ingestion)

1. Set `GARMIN_EMAIL` / `GARMIN_PASSWORD` in `.env`.
2. Seed the token cache once (interactive, handles MFA):
   ```bash
   python -m domestique_ai.export.garmin_connect
   ```
3. Activities sync every 30 minutes (auto-sync scheduler), enriched with fields,
   GPS traces and weather. The plan is exported as a **`.ZIP` of `.FIT` files** or an
   **`.ICS`** file — and a **webcal feed** keeps Apple / Google Calendar in sync as
   the plan adapts (`/api/plan/feed.ics?key=<DOMESTIQUE_AI_CALENDAR_FEED_KEY>`).

### Google Health (recovery data — Fitbit / Pixel Watch)

1. Create a Google Cloud project, enable the **Google Health API** and add the five
   read-only health scopes to the OAuth consent screen.
2. Set `GOOGLE_HEALTH_CLIENT_ID` / `GOOGLE_HEALTH_CLIENT_SECRET` in `.env`, then run
   the OAuth flow from the **Santé** page (or `GET /api/google-health/auth`).
3. HRV, resting HR, sleep + stages, SpO₂ and skin temperature sync every 6 hours.

### Ollama (LLM coach)

```bash
ollama pull gemma4:31b-cloud   # default model; override via OLLAMA_MODEL
ollama pull nomic-embed-text    # coach persistent memory; override via OLLAMA_EMBED_MODEL
ollama serve                   # or point OLLAMA_HOST at a remote endpoint
```

### Run
```bash
# Backend API (port 8501) — also serves the React build if present
uvicorn domestique_ai.api.main:app --reload --port 8501

# Frontend dev (separate terminal) — http://localhost:5173
cd frontend && npm install && npm run dev

# Production: build the front, FastAPI serves it via StaticFiles
cd frontend && npm run build
uvicorn domestique_ai.api.main:app --port 8501   # → http://localhost:8501
```

The first load redirects to `/login` — sign in with your **email**, **password** and
**TOTP code** (or a one-time recovery code).

The PWA is organised around a bottom nav: **Dashboard** (fitness state, proactive daily
brief, alerts, HR zones), **Activités** (paginated history + rich detail), **Santé**
(HRV / resting HR / sleep with a 90-day breakdown and an Apple-style hypnogram),
**Plan** (adaptive multi-week plan, coach decisions, `.ZIP` / `.ICS` export) and
**Coach** (the conversational LLM). Coaches also get **Tendances**, **Prescrire** and
**Roster** views.

### Deploy (Docker / Raspberry Pi)

```bash
docker compose up -d
```

See [DEPLOY.md](DEPLOY.md) for the Pi 5 + Tailscale setup.

</details>

---

## Roadmap

- [x] Automatic overtraining detection (HRV, resting HR, Foster Monotony/Strain)
- [x] LLM-generated training plans with deterministic guardrails
- [x] Adaptive rolling plan — daily morning check + weekly review
- [x] Graduated return when deconditioned (graduated intensity ceiling)
- [x] Google Health ingestion (HRV, sleep + stages, SpO₂, skin temp)
- [x] Garmin Connect as sole source (Strava API retired after its paid-only policy)
- [x] Enriched activities — streams, GPS, weather, source de-duplication
- [x] Similar-activity comparison ("how many times have I climbed this?")
- [x] iCalendar export + webcal subscription feed
- [x] Sleep analytics (90-day breakdown, Apple-style hypnogram)
- [x] Multi-athlete roster view for coaches
- [x] Email/password auth with mandatory TOTP 2FA, recovery codes and per-account roles
- [x] Error supervision (Sentry) + Healthchecks.io heartbeat
- [ ] Per-activity HR profile to freeze historical CTL/ATL/TSB

---

## About

I'm **Arnaud Stadler** — a Python / full-stack developer who likes turning fuzzy,
data-heavy problems into reliable products. DomestiqueAI is the kind of work I do
end to end: a real data pipeline, a domain engine I can defend on the science,
a pragmatic LLM integration that *doesn't* hallucinate, and a deployment that
actually runs in production.

This project is the kind of work I enjoy most: owning a feature end to end, from
data ingestion to a polished UI. **Always happy to talk shop** about training data,
local LLMs, or self-hosted products.

📫 Find me on my **[GitHub profile](https://github.com/arnaudstdr)**.

---

## License

MIT — see [LICENSE](LICENSE).
