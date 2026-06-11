# TWELVE Pilot

TWELVE is a browser-based AI viva pilot with:

- Admin exam setup for CSV students, problem statements, curriculum, rubrics, and submissions.
- Staff login with role-backed admin/review access, CSRF-protected cookies, and first-account bootstrap.
- One-time student exam codes with roll-number verification; plaintext codes are returned only when an exam is created.
- Student viva flow with camera, mic, fullscreen, optional screen share, spoken AI questions, server-side voice transcription, subtitles, and typed fallback.
- Proctoring flags for tab switch, window blur, fullscreen exit, camera loss/blocking, mic mute, screen-share stop, no face, multiple faces, and sustained off-center face position when the browser supports face detection.
- Professor review for transcripts, answer-level scores, scoring metadata, proctoring timeline, secure audio playback, and score override records.

## Boundary

This pilot is intentionally browser-only. It can detect and log browser-level events, but it cannot prevent students from opening other apps or devices. True lockdown requires a kiosk wrapper or secure browser later.

Proctoring flags are stored for review only. They are not used in score calculation.

The current implementation is still a hardened SQLite pilot, not the full production storage target. The API contracts and controls are aligned with the production plan, but Postgres, Redis, object storage, Alembic migrations, background jobs, Sentry/OpenTelemetry, managed backups, and WAF deployment are still infrastructure follow-ups.

## Stack

- `frontend/`: Next.js app router UI.
- `backend/`: FastAPI API with local SQLite persistence and file storage for the pilot.
- `docker-compose.yml`: Postgres and Redis services reserved for the production storage/session-state migration. The current runnable pilot uses SQLite to stay self-contained.

## Setup

```bash
python3 -m venv backend/.venv
source backend/.venv/bin/activate
pip install -r backend/requirements.txt
npm install
npm install --prefix frontend
cp .env.example .env
```

Add `OPENAI_API_KEY` in `.env` to enable model-backed viva behavior.

Set a long random `TWELVE_SECRET_KEY`; it is used to HMAC opaque session and student invite tokens. In `local` mode, cookies are not marked `Secure` so localhost development works. In staging/production, set `TWELVE_ENV=staging` or `pilot-prod` and serve the API over HTTPS so `Secure`, `HttpOnly`, `SameSite=Lax` cookies are used.

Create the first staff account from the Admin or Review login screen by switching to First setup. Alternatively, set:

```bash
TWELVE_BOOTSTRAP_ADMIN_EMAIL=admin@example.edu
TWELVE_BOOTSTRAP_ADMIN_PASSWORD=replace-with-a-long-password
```

Once any staff user exists, the bootstrap endpoint is disabled.

You can use OpenAI or Gemini for the viva intelligence. When configured, TWELVE uses the selected provider for:

- generating the five-question viva plan,
- scoring each answer against the rubric and expected points,
- deciding whether one follow-up question is needed.

The backend stores which provider was used in transcript events and scoring runs. In `local`, `development`, or `test`, a scoring provider failure can fall back to the deterministic pilot scorer. In staging/production modes, scoring provider failure records `pending_ai_error` so a professor can review or retry instead of silently receiving fake marks.

Voice answer transcription is server-side:

- `TWELVE_TRANSCRIPTION_PROVIDER=auto` uses OpenAI first when `OPENAI_API_KEY` exists, then Gemini when `GEMINI_API_KEY` exists, otherwise local mode.
- OpenAI uses `OPENAI_TRANSCRIPTION_MODEL`.
- Gemini uses `GEMINI_TRANSCRIPTION_MODEL`.
- Browser `SpeechRecognition` is only a draft preview. Voice scoring uses the transcript stored by the backend for the uploaded audio. In local/development/test without provider keys, TWELVE may mark that browser draft as `draft_used`; staging/production should configure a real provider.

Provider settings:

```bash
TWELVE_AI_PROVIDER=auto
OPENAI_VIVA_MODEL=gpt-5.5
OPENAI_VIVA_TIMEOUT_SECONDS=45
OPENAI_REALTIME_MODEL=gpt-realtime-mini
GEMINI_VIVA_MODEL=gemini-3.5-flash
GEMINI_VIVA_TIMEOUT_SECONDS=45
GEMINI_TTS_MODEL=gemini-3.1-flash-tts-preview
GEMINI_TTS_VOICE=Kore
GEMINI_TTS_TIMEOUT_SECONDS=45
GEMINI_LIVE_MODEL=gemini-3.1-flash-live-preview
GEMINI_LIVE_TOKEN_MINUTES=30
GEMINI_LIVE_NEW_SESSION_SECONDS=60
```

`TWELVE_AI_PROVIDER` can be `auto`, `openai`, `gemini`, or `local`. In `auto`, OpenAI is used first if `OPENAI_API_KEY` exists; otherwise Gemini is used if `GEMINI_API_KEY` exists; otherwise the local fallback is used.

## Voice

The current student portal voice path is hybrid:

- If `GEMINI_API_KEY` is configured, the displayed AI question is first sent to Gemini TTS and played as a cached WAV file.
- If Gemini TTS is unavailable, the displayed AI question falls back to the browser's `speechSynthesis`.
- Student voice answers are transcribed with browser `SpeechRecognition` where available.
- Recorded answer audio is uploaded and stored as an `audio_ref`.
- Typed answer fallback is always available.

Gemini and OpenAI API keys power viva question generation, scoring, and follow-up decisions. Gemini can now also power spoken question audio.

Future voice options:

- Gemini Live API can support real-time conversational voice sessions.
- OpenAI Realtime can support real-time conversational voice sessions.
- Browser speech remains the simplest pilot option because it works without exposing provider keys to the client.

Gemini Live plumbing is prepared through:

```http
POST /api/gemini/live-token
```

That endpoint creates a short-lived Live API token for browser WebSocket use. It does not expose `GEMINI_API_KEY`. A full browser Live WebSocket client is intentionally separate from the current viva flow because the existing exam flow needs deterministic question display, answer capture, scoring, and transcripts per question.

## Run

In two terminals:

```bash
source backend/.venv/bin/activate
cd backend
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

```bash
cd frontend
npm run dev
```

Open:

- Admin: http://localhost:3000/admin
- Student Viva: http://localhost:3000/student
- Professor Review: http://localhost:3000/review
- API health: http://127.0.0.1:8000/health

## CSV Format

Accepted headers include:

```csv
roll_number,name,email
23CSE001,Asha Rao,asha@example.edu
23CSE002,Rahul Mehta,rahul@example.edu
```

Submission filenames that contain a roll number are linked to that student. Unmatched submissions are still indexed as shared exam context.

When an exam is created, the Admin page displays each student's one-time exam code once. Store or export those codes at that moment; later exam reads intentionally redact them. Students start with exam, roll number, and one-time code. Refreshing an active viva restores the authenticated attempt cookie.

## Verification Checklist

- Create an exam with a CSV, rubric, problem statement, curriculum, and at least one PDF/DOCX/ZIP/TXT submission.
- Confirm anonymous users cannot load admin or review data.
- Create or log in as a staff user before using Admin or Professor Review.
- Save the one-time student codes shown immediately after exam creation.
- Start a student session after granting camera, mic, and fullscreen permissions.
- Submit one typed answer and verify it appears with score and reasoning.
- Use voice recording in a supported browser and verify the server transcript can be submitted.
- Switch tabs, exit fullscreen, stop screen share, or block the camera and verify flags appear.
- Finalize the session and inspect answer scores, transcript events, and proctoring events in Professor Review.
- Confirm final score is based on answer scores only, not proctoring flags.
- Confirm duplicate answer submission for the same question is rejected unless replayed with the same idempotency key.
