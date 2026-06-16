#!/usr/bin/env bash
#
# First-time setup for the TWELVE viva pilot. Run once to configure and install:
#   - creates .env (generates a strong TWELVE_SECRET_KEY)
#   - picks an AI provider and stores any API keys
#   - installs backend (venv + deps) and frontend (node) dependencies
#   - optionally pulls a local Ollama model
#
# After this, run ./start.sh to launch the app. Re-running setup.sh is safe; it keeps
# existing answers unless you change them.
#
# Usage:
#   ./setup.sh            # interactive
#   ./setup.sh --yes      # non-interactive: keep/derive sane defaults, no prompts
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

ASSUME_YES=0
[[ "${1:-}" == "--yes" || "${1:-}" == "-y" ]] && ASSUME_YES=1
# Only prompt when we have a terminal and weren't told to assume defaults.
INTERACTIVE=1
{ [[ $ASSUME_YES -eq 1 ]] || [[ ! -t 0 ]]; } && INTERACTIVE=0

log()  { printf '\033[1;36m[setup]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[setup] %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31m[setup] %s\033[0m\n'  "$*" >&2; exit 1; }

ask() { # ask "Prompt" "default" -> echoes the answer (default when non-interactive)
  local prompt="$1" default="${2:-}" reply
  if [[ $INTERACTIVE -eq 0 ]]; then printf '%s' "$default"; return; fi
  read -r -p "$(printf '\033[1;36m[setup]\033[0m %s [%s]: ' "$prompt" "$default")" reply || true
  printf '%s' "${reply:-$default}"
}

# Set KEY=VALUE in .env (replace existing line or append). Value-safe (no sed escaping).
set_env() {
  local key="$1" val="$2"
  [[ -f .env ]] || : > .env
  grep -v "^${key}=" .env > .env.tmp 2>/dev/null || true
  mv .env.tmp .env
  printf '%s=%s\n' "$key" "$val" >> .env
}

get_env() { # get_env KEY -> current value in .env (empty if unset)
  [[ -f .env ]] && sed -n "s/^$1=//p" .env | head -1 || true
}

# --- 0. Prerequisites -------------------------------------------------------
log "Checking prerequisites…"
command -v python3 >/dev/null || die "python3 not found. Install Python 3.11+."
command -v node    >/dev/null || die "node not found. Install Node.js 18+ (https://nodejs.org)."
command -v npm     >/dev/null || warn "npm not found — frontend deps can't be installed here. Install it (e.g. 'sudo pacman -S npm'), or the project must already have frontend/node_modules."

# --- 1. .env ----------------------------------------------------------------
if [[ ! -f .env ]]; then
  log "Creating .env from .env.example"
  cp .env.example .env
else
  log ".env already exists — updating only what you change."
fi

# Strong secret (URL-safe, no characters that need escaping). Generate when missing or
# still the .env.example placeholder; keep a real existing one.
current_secret="$(get_env TWELVE_SECRET_KEY)"
if [[ -z "$current_secret" || "$current_secret" == *replace-with* ]]; then
  set_env TWELVE_SECRET_KEY "$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  log "Generated TWELVE_SECRET_KEY"
fi

# --- 2. AI provider ---------------------------------------------------------
cat <<'EOF'

  AI provider options:
    local   - deterministic, offline, no keys (default; lower answer quality)
    gemini  - Google Gemini API (needs GEMINI_API_KEY)
    openai  - OpenAI API (needs OPENAI_API_KEY)
    ollama  - local LLM via Ollama (offline; GPU recommended)
EOF
provider="$(ask 'Which AI provider?' "$(get_env TWELVE_AI_PROVIDER || echo local)")"
case "$provider" in local|gemini|openai|ollama|auto) ;; *) warn "Unknown '$provider' — using local."; provider=local ;; esac
set_env TWELVE_AI_PROVIDER "$provider"

case "$provider" in
  gemini)
    key="$(ask 'GEMINI_API_KEY' "$(get_env GEMINI_API_KEY)")"
    [[ -n "$key" ]] && set_env GEMINI_API_KEY "$key" || warn "No Gemini key set — will fall back to local."
    set_env TWELVE_TRANSCRIPTION_PROVIDER "gemini"
    ;;
  openai)
    key="$(ask 'OPENAI_API_KEY' "$(get_env OPENAI_API_KEY)")"
    [[ -n "$key" ]] && set_env OPENAI_API_KEY "$key" || warn "No OpenAI key set — will fall back to local."
    set_env TWELVE_TRANSCRIPTION_PROVIDER "openai"
    ;;
  ollama)
    model="$(ask 'Ollama model' "$(get_env OLLAMA_MODEL || echo llama3.2:3b)")"
    set_env OLLAMA_MODEL "$model"
    set_env OLLAMA_HOST "$(get_env OLLAMA_HOST || echo http://127.0.0.1:11434)"
    # Local STT pairs well with a local LLM.
    if [[ "$(ask 'Use local faster-whisper for transcription? (y/n)' 'y')" =~ ^[Yy] ]]; then
      set_env TWELVE_TRANSCRIPTION_PROVIDER "whisper"
    fi
    ;;
  *)
    set_env TWELVE_TRANSCRIPTION_PROVIDER "$(get_env TWELVE_TRANSCRIPTION_PROVIDER || echo local)"
    ;;
esac

# --- 3. Backend deps --------------------------------------------------------
VENV="backend/.venv"
[[ -d "$VENV" ]] || { log "Creating backend virtualenv…"; python3 -m venv "$VENV"; }
# shellcheck disable=SC1091
source "$VENV/bin/activate"
log "Installing backend dependencies…"
pip install --quiet --upgrade pip
pip install --quiet -r backend/requirements.txt -r backend/requirements-dev.txt
touch "$VENV/.deps-installed"

# --- 4. Frontend deps -------------------------------------------------------
if command -v npm >/dev/null; then
  [[ -d node_modules ]] || { log "Installing root node deps…"; npm install; }
  [[ -d frontend/node_modules ]] || { log "Installing frontend node deps…"; npm install --prefix frontend; }
else
  warn "Skipped frontend deps (no npm)."
fi

# --- 5. Optional: Ollama model ----------------------------------------------
if [[ "$provider" == "ollama" ]]; then
  if command -v ollama >/dev/null; then
    model="$(get_env OLLAMA_MODEL)"
    if [[ "$(ask "Pull Ollama model '$model' now? (y/n)" 'y')" =~ ^[Yy] ]]; then
      log "Pulling $model (needs a running 'ollama serve')…"
      ollama pull "$model" || warn "Pull failed — ensure 'ollama serve' is running, then: ollama pull $model"
    fi
  else
    warn "ollama not installed. Install it (https://ollama.com), then: ollama pull $(get_env OLLAMA_MODEL)"
  fi
fi

# --- 6. Smoke test ----------------------------------------------------------
log "Verifying backend imports + DB init…"
( cd backend && python -c "from app import main, storage; storage.init_db(); print('backend OK')" ) || warn "Backend smoke test failed — check the output above."

cat <<EOF

$(printf '\033[1;32m[setup] Done.\033[0m')
  Provider:      $(get_env TWELVE_AI_PROVIDER) / transcription $(get_env TWELVE_TRANSCRIPTION_PROVIDER)
  Next steps:
    ./start.sh --seed     # run API + web, and seed a demo viva
    Open http://localhost:3000  (Admin /admin · Student /student · Review /review)
    First staff account: use "First setup" on the Admin/Review login screen.
    Demo student login:  DEMO01 / VIVA-DEMO-2026
EOF
