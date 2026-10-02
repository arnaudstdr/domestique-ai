#!/usr/bin/env bash
# strix-audit.sh — audit de sécurité Strix de domestique-ai (local, données jetables).
# Ne contient aucun secret : le token API est relu depuis .env à chaque run,
# la clé OpenRouter vient de OPENROUTER_API_KEY ou ~/.strix/cli-config.json.

set -euo pipefail

CMD=""
RUN_MODE="quick"
MAX_TURNS="200"
PORT="8501"
AUDIT_DIR="${AUDIT_DIR:-/tmp/domestique-audit}"
KEEP_SERVER=0
REUSE_SERVER=0
ASSUME_YES=0
OLLAMA_PRESET=0
BUDGET=""
RUN_NAME=""
DEFAULT_MODEL="openrouter/poolside/laguna-s-2.1:free"
# Le modèle de ~/.strix/cli-config.json est la source de vérité s'il existe
# (override possible via STRIX_LLM ou --model).
if [[ -z "${STRIX_LLM:-}" && -f "$HOME/.strix/cli-config.json" ]] && command -v python3 >/dev/null 2>&1; then
  STRIX_LLM="$(python3 -c 'import json,os;d=json.load(open(os.path.expanduser("~/.strix/cli-config.json")));print((d.get("env") or d).get("STRIX_LLM",""))' 2>/dev/null || true)"
fi
MODEL="${STRIX_LLM:-$DEFAULT_MODEL}"
EXTRA_ARGS=()
SERVER_PID=""

die() { echo "Erreur: $*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
strix-audit.sh — audit de sécurité Strix de domestique-ai (local, données jetables).

Sous-commandes :
  setup    vérifie Docker/Strix (installe Strix si absent) et la config LLM
  prepare  copie code + data dans le dossier d'audit, écrit les instructions
  serve    prépare puis lance l'app sur des copies jetables (foreground)
  scan     lance Strix contre l'app déjà en écoute
  run      prepare + serve (arrière-plan) + scan + arrêt   [défaut]
  resume   reprend le dernier run interrompu (ou --run NAME)
  view     ouvre le dashboard des résultats
  clean    supprime le dossier d'audit
  quota    affiche le quota OpenRouter restant (free + crédits)

Options :
  --mode quick|standard|deep   profondeur du scan (défaut : quick)
  --turns N                    max tours par agent (défaut : 200)
  --model ID                   modèle LiteLLM (défaut : OpenRouter free)
  --budget N                   plafond de dépense LLM en USD (modèles payants)
  --run NAME                   run à reprendre (défaut : le plus récent)
  --ollama                     preset Ollama Cloud (devstral-small-2:24b-cloud)
  --port N                     port uvicorn (défaut : 8501)
  --dir PATH                   dossier d'audit (défaut : /tmp/domestique-audit)
  --keep-server                laisse uvicorn tourner après le scan
  --reuse-server               scanne l'app déjà en écoute (instance existante !)
  --yes                        ne pas demander confirmation (clean, setup)
  -- ARGS...                   arguments passés tels quels à strix

Exemples :
  scripts/strix-audit.sh run --mode quick --turns 200
  scripts/strix-audit.sh run --ollama --keep-server
  scripts/strix-audit.sh run --model openrouter/poolside/laguna-s-2.1 --budget 5
  scripts/strix-audit.sh run --mode standard --turns 400 -- --scan-mode standard

Variables : OPENROUTER_API_KEY, STRIX_LLM, DOMESTIQUE_REPO (si hors repo).
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    setup|prepare|serve|scan|run|resume|view|clean|quota|help) CMD="$1"; shift ;;
    --mode) RUN_MODE="$2"; shift 2 ;;
    --turns) MAX_TURNS="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --budget) BUDGET="$2"; shift 2 ;;
    --run) RUN_NAME="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --dir) AUDIT_DIR="$2"; shift 2 ;;
    --ollama) OLLAMA_PRESET=1; MODEL="ollama/devstral-small-2:24b-cloud"; shift ;;
    --keep-server) KEEP_SERVER=1; shift ;;
    --reuse-server) REUSE_SERVER=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    -h|--help) CMD="help"; shift ;;
    --) shift; EXTRA_ARGS=("$@"); break ;;
    *) die "option inconnue: $1 (voir --help)" ;;
  esac
done
CMD="${CMD:-run}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${DOMESTIQUE_REPO:-$(cd "$SCRIPT_DIR/.." && pwd)}"
[[ -f "$REPO_ROOT/domestique_ai/config.py" ]] || die "REPO_ROOT invalide ($REPO_ROOT) — exporte DOMESTIQUE_REPO"
PY="$REPO_ROOT/.venv/bin/python"

# L'installeur Strix place son binaire dans ~/.strix/bin, souvent absent du
# PATH des shells non interactifs.
if [[ -x "$HOME/.strix/bin/strix" ]]; then
  PATH="$HOME/.strix/bin:$PATH"
  export PATH
fi

need() { command -v "$1" >/dev/null 2>&1 || die "$1 est requis"; }

preflight() {
  need docker; need curl; need rsync; need awk
  docker info >/dev/null 2>&1 || die "Docker Desktop n'est pas démarré"
  [[ -x "$PY" ]] || die "venv introuvable: $PY (python -m venv .venv && pip install -e '.[dev]')"
}

setup() {
  preflight
  need python3
  if ! command -v strix >/dev/null 2>&1; then
    echo "→ Strix absent."
    if [[ "$ASSUME_YES" -eq 1 ]]; then
      curl -sSL https://strix.ai/install | bash
    else
      read -rp "Installer Strix via https://strix.ai/install ? [y/N] " a
      [[ "$a" == [yY] ]] && curl -sSL https://strix.ai/install | bash || die "Strix requis"
    fi
  fi
  echo "→ strix: $(command -v strix)"
  local cfg="$HOME/.strix/cli-config.json"
  if [[ -n "${OPENROUTER_API_KEY:-}" && ! -f "$cfg" ]]; then
    mkdir -p "$HOME/.strix"
    ( umask 077
      python3 - "$cfg" <<'PY'
import json, os, sys
cfg = sys.argv[1]
data = {"env": {
    "STRIX_LLM": os.environ.get("STRIX_LLM", "openrouter/poolside/laguna-s-2.1:free"),
    "LLM_API_KEY": os.environ["OPENROUTER_API_KEY"],
    "STRIX_TELEMETRY": "0",
}}
json.dump(data, open(cfg, "w"), indent=2)
PY
    )
    chmod 600 "$cfg"
    echo "→ Config écrite: $cfg"
  elif [[ -f "$cfg" ]]; then
    echo "→ Config existante: $cfg"
  else
    echo "→ OPENROUTER_API_KEY non définie et $cfg absent — la config LLM reste à ta charge"
  fi
  docker pull ghcr.io/usestrix/strix-sandbox:1.3.0 >/dev/null 2>&1 || echo "→ (image sandbox tirée au premier run)"
}

write_instructions() {
  local token f="$AUDIT_DIR/instructions.md"
  mkdir -p "$AUDIT_DIR"
  token="$(awk -F= '/^DOMESTIQUE_AI_API_TOKEN=/{sub(/^[^=]*=/,""); gsub(/^"|"$/,""); print; exit}' "$REPO_ROOT/.env" 2>/dev/null || true)"
  [[ -n "$token" ]] || die "DOMESTIQUE_AI_API_TOKEN absent de $REPO_ROOT/.env"
  ( umask 077
    cat > "$f" <<EOF
# Autorisation
Test d'intrusion autorisé sur mon application locale (je suis propriétaire).
Cible : http://localhost:$PORT et le code source monté dans le sandbox.
Reste strictement dans ce scope, ne touche à aucun autre domaine.

# Authentification
Toutes les routes /api/* exigent sur chaque requête l'en-tête :
Authorization: Bearer $token
Ce token legacy donne le rôle coach bootstrap (bypass TOTP). Utilise-le partout.

# Focus
1. Broken access control : isolation multi-tenant (param ?athlete=, lecture seule),
   escalade athlète vers coach, accès /api/admin/* sans rôle admin.
2. Injection : import TCX via XML ElementTree (XXE, billion laughs, DoS),
   SQLi sur filtres/recherche.
3. Uploads : avatar (500 Ko, magic bytes) et TCX — path traversal, taille, stockage.
4. Fuites : GET /api/plan/feed.ics (token en query), exports ZIP/ICS,
   en-têtes de réponse et logs.
5. Auth/session : rate-limiting (sans X-Forwarded-For), CORS, TOTP,
   tokens invitation/reconnexion.
6. SSE : /api/coach/chat, /api/coach/analyze, POST /api/plan/llm.

# Contraintes
Mode $RUN_MODE. Pas de DoS volumétrique ni de brute force massif.
Chaque finding doit inclure une repro curl minimale.
EOF
  )
  chmod 600 "$f"
}

prepare() {
  preflight
  echo "→ Copie du code vers $AUDIT_DIR/src (hors .venv/node_modules/data/.env)"
  mkdir -p "$AUDIT_DIR"
  rsync -a --delete \
    --include='.env.example' \
    --exclude='.env*' \
    --exclude='/.venv/' \
    --exclude='/venv/' \
    --exclude='/node_modules/' \
    --exclude='/frontend/node_modules/' \
    --exclude='/data/' \
    --exclude='/graphify-out/' \
    --exclude='/.claude/' \
    "$REPO_ROOT/" "$AUDIT_DIR/src/"
  echo "→ Copie des données locales (DB + tokens) — copies jetables"
  mkdir -p "$AUDIT_DIR/data"
  cp -R "$REPO_ROOT/data/." "$AUDIT_DIR/data/"
  write_instructions
}

start_server() {
  if curl -fsS "http://localhost:$PORT/api/health" >/dev/null 2>&1; then
    if [[ "$REUSE_SERVER" -eq 0 ]]; then
      die "un service écoute déjà sur :$PORT — arrête-le, change de port (--port N), ou assume avec --reuse-server (tu scannes alors l'instance existante et ses vraies données)"
    fi
    echo "→ App existante réutilisée sur :$PORT (--reuse-server)"
    SERVER_PID=""
    return
  fi
  echo "→ Démarrage uvicorn :$PORT (log: $AUDIT_DIR/uvicorn.log)"
  (
    cd "$REPO_ROOT"
    exec env \
      DOMESTIQUE_AI_DB_PATH="$AUDIT_DIR/data/strava_activities.db" \
      DOMESTIQUE_AI_PLATFORM_DB_PATH="$AUDIT_DIR/data/platform.db" \
      DOMESTIQUE_AI_ATHLETES_ROOT="$AUDIT_DIR/data/athletes" \
      DOMESTIQUE_AI_SIGNUP_ENABLED=0 \
      DOMESTIQUE_AI_GARMIN_AUTO_SYNC_MINUTES=0 \
      DOMESTIQUE_AI_GARMIN_HEALTH_AUTO_SYNC_MINUTES=0 \
      DOMESTIQUE_AI_GOOGLE_HEALTH_AUTO_SYNC_MINUTES=0 \
      SENTRY_ENABLED=0 \
      "$PY" -m uvicorn domestique_ai.api.main:app --port "$PORT" --no-server-header --no-access-log \
      >"$AUDIT_DIR/uvicorn.log" 2>&1
  ) &
  SERVER_PID=$!
  local i
  for i in $(seq 1 60); do
    if curl -fsS "http://localhost:$PORT/api/health" >/dev/null 2>&1; then
      echo "→ App prête sur http://localhost:$PORT"
      return
    fi
    sleep 0.5
  done
  die "démarrage échoué — voir $AUDIT_DIR/uvicorn.log"
}

stop_server() {
  [[ -n "${SERVER_PID:-}" && "$KEEP_SERVER" -eq 0 ]] || return 0
  kill "$SERVER_PID" 2>/dev/null || true
  echo "→ uvicorn arrêté"
}
trap stop_server EXIT

fetch_spec() {
  mkdir -p "$AUDIT_DIR"
  # /openapi.json n'est plus exposé par l'API (surface d'attaque) : le schéma
  # est généré en local, hors HTTP, pour servir de contrat au scan.
  ( cd "$REPO_ROOT" && "$PY" -c \
      "import json, sys; from domestique_ai.api.main import app; json.dump(app.openapi(), open(sys.argv[1], 'w'))" \
      "$AUDIT_DIR/openapi.json" )
  echo "→ OpenAPI: $AUDIT_DIR/openapi.json"
}

configure_llm() {
  export STRIX_LLM="$MODEL"
  if [[ "$OLLAMA_PRESET" -eq 1 ]]; then
    export LLM_API_BASE="http://localhost:11434"
    echo "→ LLM: Ollama ($MODEL)"
  elif [[ -n "${OPENROUTER_API_KEY:-}" ]]; then
    export LLM_API_KEY="$OPENROUTER_API_KEY"
    echo "→ LLM: OpenRouter ($MODEL)"
  elif [[ -f "$HOME/.strix/cli-config.json" ]]; then
    echo "→ LLM: config ~/.strix/cli-config.json ($MODEL)"
  else
    die "OPENROUTER_API_KEY absente et ~/.strix/cli-config.json introuvable"
  fi
  if [[ "$MODEL" != *":free" && "$OLLAMA_PRESET" -eq 0 && -z "$BUDGET" ]]; then
    echo "  ⚠️  Modèle payant sans --budget : ajoute --budget 5 pour plafonner la dépense."
  fi
}

openrouter_key() {
  if [[ -n "${OPENROUTER_API_KEY:-}" ]]; then printf '%s' "$OPENROUTER_API_KEY"; return; fi
  [[ -f "$HOME/.strix/cli-config.json" ]] || return 0
  python3 -c 'import json,os;d=json.load(open(os.path.expanduser("~/.strix/cli-config.json")));print((d.get("env") or d).get("LLM_API_KEY",""))' 2>/dev/null || true
}

check_quota() {
  [[ "$MODEL" == openrouter/* ]] || return 0
  local key; key="$(openrouter_key)"
  [[ -n "$key" ]] || return 0
  python3 - "$key" "$MODEL" <<'PY' || true
import json, sys, urllib.request
key, model = sys.argv[1], sys.argv[2]

def get(path):
    req = urllib.request.Request(f"https://openrouter.ai/api/v1/{path}",
                                 headers={"Authorization": "Bearer " + key})
    return json.load(urllib.request.urlopen(req, timeout=10))["data"]

try:
    k = get("key")
    try:
        c = get("credits")
        balance = float(c.get("total_credits", 0)) - float(c.get("total_usage", 0))
    except Exception:
        balance = None
    f = k.get("free_model_daily_requests") or {}
    cap = f"{k.get('limit_remaining')}/{k.get('limit')}"
    if model.endswith(":free"):
        print(f"→ Free OpenRouter : {f.get('remaining')}/{f.get('limit')} requêtes restantes "
              f"(reset minuit UTC) — solde réel : {balance}$ (limite clé {cap})")
        if (f.get("remaining") or 0) < 100:
            print("  ⚠️  Un scan quick consomme ~100-300 appels : le quota free ne suffira pas.")
            print("     Options : top-up (≥9$ achetés → 1000 req/jour free), modèle payant avec --budget N, ou --ollama.")
    else:
        bal = "?" if balance is None else f"{balance:.2f}"
        print(f"→ Modèle payant : solde OpenRouter {bal}$ (limite clé {cap}) — plafonne avec --budget N.")
        if balance is not None and balance <= 0:
            print("  ⚠️  Solde à 0 → erreurs 402 : top-up sur https://openrouter.ai/settings/credits")
except Exception as e:
    print(f"→ Quota non vérifié: {e}")
PY
}

run_scan() {
  need strix
  curl -fsS "http://localhost:$PORT/api/health" >/dev/null 2>&1 \
    || die "app non joignable sur :$PORT — lance './strix-audit.sh serve' d'abord"
  configure_llm
  check_quota
  [[ -f "$AUDIT_DIR/openapi.json" ]] || fetch_spec
  write_instructions
  local args=(
    -t "$AUDIT_DIR/openapi.json"
    -t "http://localhost:$PORT"
    -t "$AUDIT_DIR/src"
    -m "$RUN_MODE"
    --max-turns "$MAX_TURNS"
    --instruction-file "$AUDIT_DIR/instructions.md"
  )
  [[ -n "$BUDGET" ]] && args+=(--max-budget "$BUDGET")
  echo "→ Scan Strix ($RUN_MODE, max $MAX_TURNS tours${BUDGET:+, budget \$$BUDGET})"
  local status=0
  ( cd "$AUDIT_DIR" && strix "${args[@]}" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} ) || status=$?
  handle_scan_status "$status"
  echo "→ Résultats: $AUDIT_DIR/strix_runs/"
}

handle_scan_status() {
  local status="$1"
  if [[ "$status" -eq 2 ]]; then
    echo "→ Vulnérabilités trouvées (exit 2 en headless) — voir $AUDIT_DIR/strix_runs/."
  elif [[ "$status" -ne 0 ]]; then
    echo "→ Strix a échoué (code $status). Causes fréquentes : rate limit free, erreur provider (400 tool call)."
    echo "  Options : --ollama ; meilleur modèle (--model openrouter/deepseek/deepseek-v4-pro --budget N) ;"
    echo "  ou 'scripts/strix-audit.sh resume' si l'état du run est récupérable."
    return "$status"
  fi
  return 0
}

run_resume() {
  need strix
  curl -fsS "http://localhost:$PORT/api/health" >/dev/null 2>&1 \
    || die "app non joignable sur :$PORT — lance './strix-audit.sh serve' d'abord"
  local run="$RUN_NAME"
  if [[ -z "$run" ]]; then
    run="$(/bin/ls -t "$AUDIT_DIR/strix_runs" 2>/dev/null | head -1)"
  fi
  [[ -n "$run" ]] || die "aucun run dans $AUDIT_DIR/strix_runs"
  [[ -f "$AUDIT_DIR/strix_runs/$run/.state/agents.json" ]] \
    || die "run '$run' non reprisable (pas de .state/agents.json)"
  configure_llm
  check_quota
  local args=(--resume "$run" --max-turns "$MAX_TURNS")
  [[ -n "$BUDGET" ]] && args+=(--max-budget "$BUDGET")
  echo "→ Reprise du run $run ($MODEL, max $MAX_TURNS tours${BUDGET:+, budget \$$BUDGET})"
  local status=0
  ( cd "$AUDIT_DIR" && strix "${args[@]}" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} ) || status=$?
  handle_scan_status "$status"
  echo "→ Résultats: $AUDIT_DIR/strix_runs/$run/"
}

remove_audit_dir() {
  # macOS : ACL "deny delete" (souvent posée sur .git) et flags uchg bloquent rm -rf.
  command -v chflags >/dev/null 2>&1 && chflags -R nouchg "$AUDIT_DIR" 2>/dev/null || true
  chmod -RN "$AUDIT_DIR" 2>/dev/null || true
  chmod -R u+rwX "$AUDIT_DIR" 2>/dev/null || true
  rm -rf "$AUDIT_DIR" || die "suppression partielle de $AUDIT_DIR — vérifie ACL/flags"
  echo "→ $AUDIT_DIR supprimé"
}

case "$CMD" in
  setup) setup ;;
  prepare) prepare ;;
  serve)
    prepare
    start_server
    if [[ -n "$SERVER_PID" ]]; then echo "→ Ctrl-C pour arrêter"; wait "$SERVER_PID"; else echo "→ App déjà lancée"; fi
    ;;
  scan) run_scan ;;
  resume)
    start_server
    run_resume
    echo "→ Dashboard: cd $AUDIT_DIR && strix view"
    ;;
  run)
    prepare
    start_server
    run_scan
    echo "→ Dashboard: cd $AUDIT_DIR && strix view"
    ;;
  view)
    [[ -d "$AUDIT_DIR" ]] || die "pas de dossier $AUDIT_DIR"
    ( cd "$AUDIT_DIR" && strix view )
    ;;
  clean)
    [[ -d "$AUDIT_DIR" ]] || { echo "Rien à nettoyer"; exit 0; }
    if [[ "$ASSUME_YES" -eq 0 ]]; then
      read -rp "Supprimer $AUDIT_DIR ? [y/N] " a
      [[ "$a" == [yY] ]] || { echo "Annulé"; exit 0; }
    fi
    remove_audit_dir
    ;;
  quota) check_quota ;;
  help|*) usage ;;
esac
