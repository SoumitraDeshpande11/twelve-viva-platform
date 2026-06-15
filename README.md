<div align="center">

# TWELVE — AI Viva Pilot

**A browser-based platform for AI-led oral examinations: structured questioning, voice and typed answers, live proctoring signals, and a full professor review trail.**

[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Next.js 16](https://img.shields.io/badge/Next.js%2016-000000?logo=nextdotjs&logoColor=white)](https://nextjs.org/)
[![Docker Compose](https://img.shields.io/badge/Docker%20Compose-2496ED?logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![pytest](https://img.shields.io/badge/pytest%20·%2015%20passing-3776AB?logo=pytest&logoColor=white)](backend/tests)
[![License](https://img.shields.io/badge/Pilot-Browser--only-6E56CF)](#architecture--boundaries)

<br/>

<sub>**Built with**</sub>

![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Pydantic](https://img.shields.io/badge/Pydantic-E92063?style=for-the-badge&logo=pydantic&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?style=for-the-badge&logo=sqlite&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?style=for-the-badge&logo=typescript&logoColor=white)
![React](https://img.shields.io/badge/React%2019-61DAFB?style=for-the-badge&logo=react&logoColor=black)
![Next.js](https://img.shields.io/badge/Next.js-000000?style=for-the-badge&logo=nextdotjs&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![OpenAI](https://img.shields.io/badge/OpenAI-412991?style=for-the-badge&logo=openai&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini-8E75B2?style=for-the-badge&logo=googlegemini&logoColor=white)

</div>

---

## Overview

TWELVE runs an end-to-end viva (oral exam) in the browser. An administrator sets up an exam from a student roster, rubric, problem statement, and project submissions. Each student joins with a one-time code, grants camera / microphone / fullscreen, and is taken through an AI-generated five-question plan with optional follow-ups. Answers — spoken or typed — are transcribed and scored against the rubric, while proctoring signals are logged for audit. A professor then reviews transcripts, per-answer scores and reasoning, the proctoring timeline, secure audio playback, and can override the final grade.

```
┌──────────┐      ┌──────────────────────┐      ┌──────────────┐
│  Admin   │─────▶│      FastAPI API      │◀────▶│  AI provider │
│  setup   │      │  (SQLite + storage)   │      │ OpenAI/Gemini│
└──────────┘      │                       │      │  + local fb  │
┌──────────┐      │  auth · sessions ·    │      └──────────────┘
│ Student  │◀────▶│  scoring · proctoring │
│  viva    │      │  · review · retries   │      ┌──────────────┐
└──────────┘      │                       │─────▶│   Gemini TTS │
┌──────────┐      │                       │      │ transcription│
│Professor │◀────▶│                       │      └──────────────┘
│ review   │      └──────────────────────┘
└──────────┘
```

---

## Features

| Area | What it does |
| --- | --- |
| **Exam setup** | CSV roster, rubric, problem statement, curriculum, and PDF/DOCX/ZIP/TXT submissions indexed as viva context. |
| **One-time access** | Per-student exam codes (shown once at creation), roll-number verification, optional **time windows** (`opens at` / `closes at`). |
| **AI questioning** | Five-question plan generated per student; one follow-up decided per base question. OpenAI, Gemini, or a deterministic local fallback. |
| **Answers** | Spoken (server-side transcription) or typed, with idempotent submission and per-question scoring against the rubric. |
| **Proctoring** | Logs tab switch, blur, fullscreen exit, camera/mic loss, screen-share stop, no/multiple faces, off-center gaze. Audit-only — never affects the score. |
| **Professor review** | Transcripts, answer scores + reasoning, proctoring timeline, secure audio playback, and score overrides. |
| **Score authority** | `mark_mode` drives an `official` / `provisional` status: AI-official on completion, or provisional until a professor approves. Overrides surface an `effective_score` without mutating the AI audit value. |
| **Recovery** | Staff can re-score answers stuck on an AI error and re-transcribe failed audio (which re-syncs the answer and re-scores it). |

---

## Quick start (Docker)

The whole stack — API and web — runs with one command. No manual server juggling.

```bash
cp .env.example .env          # optional: add OPENAI_API_KEY / GEMINI_API_KEY
docker compose up -d --build
```

| Service | URL |
| --- | --- |
| Web app | http://localhost:3000 |
| API | http://localhost:8000 |
| Health | http://localhost:8000/health |

| Task | Command |
| --- | --- |
| Start | `docker compose up -d` |
| Rebuild after a change | `docker compose up -d --build` |
| Logs | `docker compose logs -f` |
| Stop | `docker compose down` |
| Stop and wipe the SQLite data | `docker compose down -v` |

SQLite lives on a named volume, so data survives restarts. Postgres and Redis are reserved for the production migration and sit behind a profile; start them only if you need them:

```bash
docker compose --profile prod-infra up -d
```

---

## Quick start (manual)

For local development without Docker, in two terminals:

```bash
# API
python3 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env
cd backend && uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

```bash
# Web
npm install --prefix frontend
cd frontend && npm run dev
```

Open **Admin** at `/admin`, **Student Viva** at `/student`, **Professor Review** at `/review`.

The first staff account is created from the Admin or Review login screen via **First setup**, or by setting `TWELVE_BOOTSTRAP_ADMIN_EMAIL` / `TWELVE_BOOTSTRAP_ADMIN_PASSWORD` before first launch. Once any staff user exists, the bootstrap endpoint is disabled.

---

## Configuration

Set a long, random `TWELVE_SECRET_KEY` — it HMACs opaque session and student-invite tokens. In `local` mode cookies are not marked `Secure` so localhost works; set `TWELVE_ENV=staging` (or `pilot-prod`) and serve over HTTPS in deployed environments to get `Secure`, `HttpOnly`, `SameSite=Lax` cookies.

### AI providers

`TWELVE_AI_PROVIDER` is `auto` | `openai` | `gemini` | `local`. In `auto`, OpenAI is used if `OPENAI_API_KEY` is set, otherwise Gemini if `GEMINI_API_KEY` is set, otherwise the local fallback. The provider used is recorded in transcript events and scoring runs. In `local` / `development` / `test`, a provider scoring failure falls back to the deterministic local scorer; in staging/production it records `pending_ai_error` for professor review or retry instead of inventing a grade.

```bash
TWELVE_AI_PROVIDER=auto
OPENAI_VIVA_MODEL=gpt-5.5
GEMINI_VIVA_MODEL=gemini-3.5-flash
GEMINI_TTS_MODEL=gemini-3.1-flash-tts-preview
GEMINI_TTS_VOICE=Kore
```

### Voice

Spoken questions use Gemini TTS when configured, falling back to the browser's `speechSynthesis`. Student answers are transcribed server-side (`TWELVE_TRANSCRIPTION_PROVIDER=auto` prefers OpenAI, then Gemini, then local). Browser `SpeechRecognition` is a draft preview only; scoring always uses the server-stored transcript for uploaded audio. A real-time Gemini Live token endpoint (`POST /api/gemini/live-token`) is prepared but kept separate from the deterministic exam flow.

See [`.env.example`](.env.example) for the full set of variables.

---

## CSV format

```csv
roll_number,name,email
23CSE001,Asha Rao,asha@example.edu
23CSE002,Rahul Mehta,rahul@example.edu
```

Submission filenames containing a roll number are linked to that student; unmatched submissions are still indexed as shared exam context. When an exam is created, the Admin page shows each student's one-time code **once** — export them at that moment, as later reads redact them.

---

## Testing

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest -q
```

The suite (15 tests) covers the full viva flow, idempotent submission, exam time windows and mixed-offset validation, `mark_mode` official/provisional behaviour, score-override effective score and student privacy, override tie-break consistency, recovery from `pending_ai_error`, and audio re-transcription re-syncing and re-scoring the answer.

---

## Architecture & boundaries

- `frontend/` — Next.js App Router UI (admin, student, review).
- `backend/` — FastAPI API with local SQLite persistence and file storage.
- `docker-compose.yml` — `api` + `web` services; Postgres/Redis reserved behind the `prod-infra` profile.

This pilot is intentionally browser-only. It detects and logs browser-level events but cannot stop a student from opening other apps or devices; true lockdown needs a kiosk wrapper or secure browser. Proctoring flags are stored for review only and never enter score calculation.

The current implementation is a hardened SQLite pilot. The API contracts and controls are aligned with the production plan, but Postgres, Redis, object storage, Alembic migrations, background jobs, Sentry/OpenTelemetry, managed backups, and WAF deployment remain infrastructure follow-ups.

---

## Verification checklist

- [ ] Create an exam with a CSV, rubric, problem statement, curriculum, and at least one submission.
- [ ] Confirm anonymous users cannot load admin or review data.
- [ ] Save the one-time student codes shown immediately after exam creation.
- [ ] Start a student session after granting camera, mic, and fullscreen.
- [ ] Submit a typed answer; verify it appears with score and reasoning.
- [ ] Record a voice answer in a supported browser; verify the server transcript submits.
- [ ] Trigger proctoring flags (tab switch, fullscreen exit, screen-share stop, camera block) and confirm they appear.
- [ ] Finalize and inspect answer scores, transcript events, and proctoring events in review.
- [ ] Confirm the final score derives from answer scores only, not proctoring flags.
- [ ] Override a score and confirm the student sees the effective grade but not the reviewer or reason.
