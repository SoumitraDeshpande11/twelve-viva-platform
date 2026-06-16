# Explained: "Your viva recording didn't upload" bug

## Symptom

At the end of a viva, the student saw:

> Your viva recording didn't upload.
> Stay on this page and retry so your examiner can review it.

Every real recording upload failed. The retry button did nothing useful because the
server kept rejecting the request.

## How recording is supposed to work

The full-viva webcam recording is **review-only** — it is stored for an examiner to
watch back and is **never scored**. To keep memory and network bounded, the frontend
records the whole session into a buffer and uploads **one WebM file at the very end**:

- `frontend/app/student/hooks/useSessionRecorder.ts` records the existing camera stream
  into chunks while the viva is `active`.
- When the session becomes `completed`, the hook stops the recorder, assembles the
  chunks into one `Blob`, and POSTs it to
  `POST /api/student/attempts/current/recording`.
- The backend (`store_session_recording` in `backend/app/main.py`) saves the file under
  `UPLOAD_DIR/recordings/{session}/` and inserts a `session_recordings` row.

## Root cause

A timing mismatch between **when** the upload fires and **what state** the backend
required.

The upload fires on completion (`useSessionRecorder.ts`):

```ts
if (status === "completed") {
  void stopAndUpload();
}
```

But the backend guarded the endpoint with an **active-only** check:

```python
if session["status"] != "active":
    raise HTTPException(status_code=409, detail="Session is not active")
```

By the time the recording is flushed, the viva has already been finalized, so the
session status is `completed` — not `active`. The guard therefore returned **409
"Session is not active"** for the normal, expected upload path. The frontend caught the
error, kept the blob, and showed the "didn't upload" banner. Retrying hit the same 409.

In short: the recording is designed to upload at the *end*, but the server only allowed
uploads in the *middle*. The two could never line up.

A test had even encoded the bug — `test_upload_recording_rejected_when_not_active`
asserted that a completed session returns 409, locking in the broken behaviour.

## The fix

Accept both `active` and `completed`. Active still covers the early-flush case (e.g. the
500 MB cap is reached mid-viva); completed is the common end-of-viva case.

`backend/app/main.py`:

```python
# The full-viva recording is flushed at the END of the viva, so the session is
# normally already `completed` by the time this fires. Accept both active (mid-viva
# cap reached / early flush) and completed (the common case); reject anything else.
if session["status"] not in ("active", "completed"):
    raise HTTPException(status_code=409, detail="Session is not active")
```

The auth check (`require_student_attempt`) already allowed completed sessions, so no
other change was needed — the student's attempt cookie still resolves the session right
after finalize.

### Test change

`backend/tests/test_api.py`: the bug-encoding test was flipped to assert the correct
behaviour:

- `test_upload_recording_rejected_when_not_active` →
  `test_upload_recording_accepted_when_completed`
- It now asserts the upload returns **200** and that a `session_recordings` row is
  written for the session.

All 6 recording tests pass.

## Invariants preserved

- Recording stays **review-only** and never enters score math.
- Recording refs are never exposed to students; playback is staff-only and path-jailed
  (`GET /api/review/recording?ref=`).
- Exam deletion still removes the recording files.
- Footage is never silently lost: a failed upload is retained client-side and retryable.

## Verifying

1. Restart the backend so the change is loaded
   (`uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`).
2. Take a viva end to end as a student.
3. On completion the "didn't upload" banner should not appear.
4. As staff, open `/review` → the session → **Recording** tab and play it back.
