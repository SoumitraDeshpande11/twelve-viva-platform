#!/usr/bin/env bash
#
# One-command dev startup for the TWELVE viva pilot.
# Bootstraps the backend venv + deps, then launches the FastAPI API
# (127.0.0.1:8000) and Next.js web (:3000) together. Ctrl-C stops both.
#
# Works WITHOUT npm: the API runs from the backend venv and the web runs the
# Next.js binary directly via node. npm is used only to install frontend deps
# when they are missing (and only if npm is available).
#
# Usage:
#   ./start.sh            # bootstrap if needed, then run both servers
#   ./start.sh --fresh    # force-reinstall backend deps first
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

FRESH=0
SEED=0
for arg in "$@"; do
  case "$arg" in
    --fresh) FRESH=1 ;;
    --seed)  SEED=1 ;;
  esac
done

log()  { printf '\033[1;36m[start]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[start] %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31m[start] %s\033[0m\n'  "$*" >&2; exit 1; }

command -v python3 >/dev/null || die "python3 not found on PATH."
command -v node    >/dev/null || die "node not found on PATH (install Node.js)."

# .env lives at the repo root (backend loads it explicitly; uvicorn does not).
if [[ ! -f .env ]]; then
  log ".env missing — copying from .env.example (edit it to add API keys)."
  cp .env.example .env
fi

# --- Backend: venv + Python deps -------------------------------------------
VENV="backend/.venv"
if [[ $FRESH -eq 1 || ! -d "$VENV" ]]; then
  log "Creating backend virtualenv ($VENV)…"
  python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

if [[ $FRESH -eq 1 || ! -f "$VENV/.deps-installed" ]]; then
  log "Installing backend dependencies…"
  pip install --quiet --upgrade pip
  pip install --quiet -r backend/requirements.txt -r backend/requirements-dev.txt
  touch "$VENV/.deps-installed"
fi

# --- Optional: seed a ready-to-take demo viva -------------------------------
if [[ $SEED -eq 1 ]]; then
  log "Seeding demo viva…"
  ( cd backend && python seed_demo.py )
fi

# --- Frontend: node deps ----------------------------------------------------
if [[ ! -d frontend/node_modules || ! -f frontend/node_modules/next/dist/bin/next ]]; then
  if command -v npm >/dev/null; then
    log "Installing frontend node deps…"
    npm install --prefix frontend
  else
    die "frontend/node_modules missing and npm not found. Install npm (e.g. 'sudo pacman -S npm'), then run: npm install --prefix frontend"
  fi
fi

# --- Run both servers -------------------------------------------------------
NEXT_BIN="$ROOT/frontend/node_modules/next/dist/bin/next"

# Start the API in the background (uses the active venv's uvicorn).
log "Starting API on http://127.0.0.1:8000 …"
( cd backend && exec uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 ) &
API_PID=$!

# Stop both processes on Ctrl-C / exit.
cleanup() {
  log "Shutting down…"
  kill "$API_PID" 2>/dev/null || true
  wait "$API_PID" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# Run the web server in the foreground (Ctrl-C lands here -> trap kills the API).
log "Starting web on http://localhost:3000 … (Ctrl-C stops both)"
( cd frontend && exec node "$NEXT_BIN" dev -p 3000 )
