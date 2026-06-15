# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

TWELVE is a browser-based AI viva (oral exam) pilot. Admins create exams from a student CSV + problem statement + curriculum + rubric + submissions. Students authenticate with an exam code + roll number, run a proctored viva (camera/mic/fullscreen) where an AI generates questions, transcribes spoken answers, and scores them against the rubric. Professors review transcripts, scores, proctoring flags, and can override marks.

Two top-level apps:
- `backend/` — FastAPI + **SQLite** persistence + local-file storage (`backend/app/storage.py:DATA_DIR`).
- `frontend/` — Next.js app-router UI (`/admin`, `/student`, `/review`).

`docker-compose.yml` (Postgres + Redis) is **reserved for future production migration and is NOT used by the running pilot.** The pilot is deliberately self-contained on SQLite. Do not wire the app to Postgres/Redis unless explicitly migrating.

## Commands

Backend (run from `backend/`):
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
pytest                              # full suite (testpaths=tests)
pytest tests/test_api.py::test_full_flow_completes_with_final_score   # single test
```
Tests force a self-contained env in `backend/conftest.py` (`TWELVE_ENV=test`, `TWELVE_AI_PROVIDER=local`, fresh tmp SQLite DB + upload dir per test). No API keys or network needed.

Frontend (run from repo root or `frontend/`):
```bash
npm install && npm install --prefix frontend
npm run dev          # concurrently runs API (uvicorn) + web (next dev :3000)
npm run lint         # next lint
npm run typecheck    # tsc --noEmit  (frontend only; backend has no type-check step)
npm run build        # next build
```
Config lives in `.env` at **repo root** (copy from `.env.example`). `backend/app/main.py` calls `load_dotenv(parents[2]/.env)` because uvicorn does not load it. `.env` changes need a backend restart (loaded once at import; `--reload` only watches `.py`).

One-command dev + demo:
```bash
./start.sh [--fresh] [--seed]   # bootstrap venv+node deps, run API+web; --seed adds a demo viva
python backend/seed_demo.py     # ready-to-take demo (DEMO01 / VIVA-DEMO-2026); loads repo-root .env
```

## Architecture

### Backend — single-module API
`backend/app/main.py` (~1700 lines) holds every route, request/response model, and auth helper. Supporting modules:
- `agent.py` — deterministic **local** scorer/question/follow-up (keyword-overlap heuristics). The always-available fallback.
- `openai_agent.py`, `gemini_agent.py`, `ollama_agent.py` — provider-backed equivalents. Each raises `OpenAIAgentError` / `GeminiAgentError` / `OllamaAgentError` and exposes a `*_configured()` check. `ollama_agent.py` is a **local LLM** via Ollama's OpenAI-compatible endpoint (`OLLAMA_HOST`, `OLLAMA_MODEL`, default `llama3.2:3b`).
- `gemini_voice.py` — Gemini TTS (question audio WAV) + Gemini Live ephemeral token minting.
- `transcription.py` — server-side audio→text (OpenAI/Gemini/**faster-whisper**/local), provider chosen by `transcription_provider()`. On any provider error in local/dev it degrades to the browser draft (never surfaces 503/429); staging/prod re-raises.
- `file_processing.py` — CSV student parsing + PDF/DOCX/ZIP/TXT text extraction.
- `auth.py` — opaque token gen, HMAC hashing, Argon2 password hashing, cookie options.
- `storage.py` — SQLite connect/init/migrate, JSON column helpers, `utc_now()`.

### Provider dispatch (the core indirection)
AI behavior is **never called directly** — it goes through dispatcher functions in `main.py` that pick a provider and degrade gracefully:
- `selected_ai_provider()` resolves `TWELVE_AI_PROVIDER` (`auto`|`openai`|`gemini`|`ollama`|`local`). `auto` = OpenAI if keyed, else Gemini if keyed, else local (never auto-picks ollama; it is opt-in). Each provider has a branch in the three dispatcher functions that falls back on its `*AgentError`.
- `make_question_plan()`, `score_current_answer()`, `create_next_followup()` try the provider, then fall back.

**Critical fallback rule** (`local_ai_allowed()` = env in `{local, development, test}`):
- In local/dev/test, a provider failure falls back to the deterministic local scorer (`local-fallback`).
- In staging/production, a scoring failure does **NOT** fake a score — it records `scoring_status = pending_ai_error` (`pending_ai_error_result()`) so a professor must review or retry. Preserve this; never make staging/prod silently fall back to local marks.

Every scored answer / plan / follow-up records which provider produced it (`scorer_provider`, `agent_provider`, etc.) plus error text, and writes a row to `scoring_runs`.

### Dual route surface — student uses cookie-scoped routes
Many endpoints exist in two forms:
- Legacy/explicit: `/api/sessions/{session_id}/...` (session id in URL).
- Student attempt: `/api/student/attempts/current/...` — resolves the session from the authenticated student attempt cookie (`require_student_attempt`). This is what the student frontend uses; refreshing a viva restores the attempt via cookie.

### Auth & access control
- Cookie-based: `AUTH_COOKIE` (opaque session, HMAC-hashed server-side) + `CSRF_COOKIE`. Non-safe methods require a matching CSRF header (`get_auth_session(require_csrf=...)`).
- Staff roles: `super_admin`, `exam_admin`, `examiner`, `invigilator`. Gate with `require_staff(request, {roles})`.
- Students get a separate session role (`"student"`); gate with `require_student_attempt`. `require_session_access` covers both.
- First staff account is bootstrapped via `/api/auth/bootstrap` or `TWELVE_BOOTSTRAP_ADMIN_*`; the endpoint self-disables once any user exists.
- Cookie `Secure` flag is driven by `TWELVE_ENV` (off for `local`) — keep localhost dev working.
- `audit_event()` writes to `security_audit_events` for sensitive actions.

### Key invariants (don't break)
- **Final score is answer-scores only.** Proctoring flags **and the viva video recording** are stored for review and MUST NOT enter score math (`finalize_session()` averages answer score/max_score to a 0–100%).
- **Viva recording is review-only.** Full-viva webcam video+audio is uploaded to `session_recordings` (additive table), stored under `UPLOAD_DIR/recordings/{session}/`, served by staff-only path-jailed `GET /api/review/recording?ref=` (same jail as `review_audio`). Exam delete removes the files. Never expose recording refs to students.
- **Logout is audited and role-agnostic**: writes `student_logout`/`staff_logout` and clears both auth+CSRF cookies.
- **Mark modes**: exam `mark_mode` is `ai_official` (AI score is official on completion) vs `professor_approved` (provisional until a professor override). Overrides record an effective score without mutating the original `final`; override *reasons* are hidden from students.
- **Answer idempotency**: duplicate answer for the same question is rejected unless replayed with the same idempotency key.
- **Transcript hash chain**: `log_transcript()` chains each event with `prev_hash`/`event_hash` (sha256 over sequence + payload). Append via this helper, never raw-insert, or the chain breaks.
- **One-time student codes** are returned in plaintext **only** at exam creation; later reads redact them.
- **Exam windows**: `starts_at`/`ends_at` gate student start.
- DB schema evolves via `storage.py:migrate_db()` / `add_column()` (additive, idempotent) — there are no Alembic migrations.

### Frontend
`frontend/lib/api.ts` is the single typed API client (credentials included for cookies). `API_BASE` resolves to the **browsing host** in local dev (localhost vs 127.0.0.1) so the `SameSite=Lax` auth cookie isn't dropped cross-site; a non-local `NEXT_PUBLIC_API_BASE` is used as-is. Pages: `app/admin`, `app/student`, `app/review`, with `AuthPanel.tsx` for staff login; `AppShell.tsx` has the student/staff logout. Voice path is hybrid: Gemini TTS WAV for questions (fallback browser `speechSynthesis`, with play/pause/replay), browser `SpeechRecognition` draft + server-side transcription of uploaded audio as the scored source of truth.

Student viva hooks (`app/student/hooks/`): `useVivaSession` (lifecycle + logging), `useMediaCapture` (camera/mic/fullscreen), `useProctoring` (browser-event flags, throttled), `useSessionRecorder` (records the camera stream → uploads one WebM at the end → `/review` Recording tab), `useFaceLighting` (lightweight brightness check), `useQuestionTts`, `useVoiceRecorder`. Fullscreen is enforced on start (`canStart` + a hard guard) and re-prompted via a gate if exited mid-viva; leaving the window (alt-tab) shows a gate too. `next.config.ts` sets `optimizePackageImports: ["@phosphor-icons/react"]` (dev compile speed).
