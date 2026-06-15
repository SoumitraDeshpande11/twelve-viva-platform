# Spec: Viva video recording for examiner review

Status: **Approved — ready to implement**
Owner: TBD
Branch: `feat/audit-fixes`
Date: 2026-06-16

## 1. Problem

A professor reviewing a viva sees the transcript, scores, and browser proctoring
flags (tab/blur/fullscreen/face heuristics), but **cannot watch the student**. The
heuristic flags are coarse and cannot tell "thinking" from "cheating." The decision
on whether a glance away is benign is best made by a **human watching the footage**,
not by the AI.

So: record the student's webcam (video + audio) for the whole viva and let the
examiner play it back in `/review`. The AI does **not** judge gaze/cheating — it only
captures the evidence for the examiner.

## 2. Goals

- Record the student's camera (video + audio) continuously during an active viva.
- Upload the recording at the end and store it server-side (local file, like audio).
- Let staff play the recording back in `/review`.
- Explicit consent for video recording.
- Preserve invariants: recording is **review-only, never scored**; one camera stream
  (no second `getUserMedia`); a failed upload is retained and retryable.

## 3. Non-goals

- AI gaze / eye-tracking / "cheating" detection. The examiner decides from the video.
- Live streaming / real-time proctor view. Upload-at-end only.
- Editing, redaction, or per-question video segmentation. One file per viva.
- Cloud/object storage. Local-file under `UPLOAD_DIR`, consistent with the pilot.

## 4. Decisions (resolved during brainstorming)

- **Capture**: browser `MediaRecorder` on the existing camera stream → one continuous
  WebM → uploaded once at the end.
- **Quality / size**: target **720p, ~2.5 Mbps** (`video/webm`, VP8/VP9 + Opus); hard
  cap **~500 MB** (`TWELVE_MAX_VIDEO_BYTES`). On hitting the cap, stop recording and
  log a proctoring flag.
- **Gaze**: not built. Examiner judgement via the recording.

## 5. Design

### 5.1 Capture — `useSessionRecorder` (frontend)

New hook `frontend/app/student/hooks/useSessionRecorder.ts`:

- Inputs: the active `session`, the camera `MediaStream` (from `useMediaCapture`),
  `logLiveTurnEvent`/`logEvent`, and an `onUploaded` callback.
- Starts a `MediaRecorder(stream, { mimeType: pickSupportedWebm(), videoBitsPerSecond })`
  when the session becomes `active` **and** a camera stream exists. Records **video +
  audio** (the camera stream already carries both).
- `start(timeslice = 5000)` accumulates `Blob` chunks in a ref. Running total of bytes
  is tracked; if it exceeds the cap, `stop()` and log `recording_cap_reached` (warning).
- On finalize (viva completes) or unmount, `stop()`, assemble one `Blob`, and POST it.
- Reuses the audio hook's **retain-and-retry**: keep the blob until the upload
  succeeds; expose `uploadFailed` + a manual retry; never silently drop footage.
- If `MediaRecorder` or a WebM mime type is unsupported, skip recording and log
  `recording_unsupported` (info) once — never block the viva.

Resolution/bitrate: request the camera at 1280×720 ideal in `useMediaCapture`
constraints (already video:true; tighten to `{ width: 1280, height: 720 }` ideal),
`videoBitsPerSecond: 2_500_000`.

### 5.2 Upload + storage (backend)

Endpoints (mirror the audio pair):

- `POST /api/student/attempts/current/recording` — cookie-scoped (`require_student_attempt`).
- `POST /api/sessions/{session_id}/recording` — legacy/explicit (`require_session_access`,
  `student_only=True`).

Both call `store_session_recording(session_id, upload)`:

- Reject if session not `active` (409) or file > `TWELVE_MAX_VIDEO_BYTES` (413, default
  `500 * 1024 * 1024`).
- Write to `UPLOAD_DIR/recordings/{session_id}/{uuid}.webm`.
- Insert a row into a new table and log a `recording_uploaded` transcript event.

New table (additive via `storage.py:migrate_db`, idempotent):

```
session_recordings (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES viva_sessions(id) ON DELETE CASCADE,
  storage_path TEXT NOT NULL,
  mime_type TEXT,
  size_bytes INTEGER NOT NULL,
  created_at TEXT NOT NULL
)
```

Exam deletion already removes a session's audio/files; extend it to delete
`recordings/{session_id}` files too (DB rows cascade via the FK).

### 5.3 Review (backend + frontend)

- `GET /api/review/recording?ref=` — `require_staff({super_admin, examiner, invigilator})`,
  resolve `ref`, reject if not under `UPLOAD_DIR` (path-jail, same as `review_audio`),
  return `FileResponse(..., media_type="video/webm")`.
- `review_session` detail payload gains `recordings: [{ id, ref, size_bytes, created_at }]`
  (ref = `storage_path`, served via the endpoint above).
- `/review` page: a new **Recording** tab rendering `<video controls preload="metadata"
  src={`${API_BASE}/api/review/recording?ref=...`} />`. If no recording, show a muted
  "No recording captured" note. Existing tabs (summary/answers/proctoring/transcript)
  unchanged.

### 5.4 Consent

`CONSENT_TEXT` (student page) currently lists "audio recording, transcripts…". Add
**video recording** explicitly:

> "I consent to browser proctoring flags, **camera video and audio recording**,
> transcripts, AI scoring, and professor review for this viva."

## 6. Edge cases

- **Camera lost mid-viva**: `MediaRecorder` stops on track end; upload whatever was
  captured. A `media_unavailable` flag already logs.
- **Upload failure at end**: retain the blob, surface `uploadFailed`, allow retry; if
  the student leaves first, the footage is lost (best-effort, logged).
- **Cap reached**: stop recording, log `recording_cap_reached`; the partial file still
  uploads.
- **Unsupported browser**: skip, log `recording_unsupported`; viva proceeds normally.
- **Large upload**: 413 if over the cap; the client should also pre-check blob size and
  warn rather than POST a doomed request.

## 7. Testing (backend, pytest)

- Upload stores a `session_recordings` row + the file on disk; rejects when > cap (413)
  and when the session is not active (409).
- `review/recording` serves the file for staff; returns 403 for a ref outside
  `UPLOAD_DIR`; 404 for a missing file; 401/403 for non-staff.
- Exam deletion removes the recording rows (cascade) and the on-disk files.

Frontend: manual (no JS test harness). Verify record → upload → playback on the demo
viva; verify graceful skip when `MediaRecorder` is unavailable.

## 8. Open questions

None — capture method, quality/cap, and the no-AI-gaze decision are resolved (§4).
