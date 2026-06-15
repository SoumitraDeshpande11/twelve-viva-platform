# Spec: Student login/logout & session switching

Status: **Draft — not implemented**
Owner: TBD
Branch: `feat/audit-fixes`
Date: 2026-06-15

## 1. Problem

Two related usability/auth gaps surfaced during pilot testing:

1. **A student cannot log out from the UI.** A student "logs in" by entering an
   exam code + roll number + one-time code on `/student`, which mints a student
   session cookie (`require_student_attempt`). But `AppShell` only renders the
   account/logout controls when `me.role === "staff"`, so a student has no way to
   end their session — important on shared/lab machines where the next student
   must not inherit the previous attempt's cookie.

2. **There is no clear way to switch roles.** Staff are (correctly) blocked from
   sitting a viva (`start_session` rejects a staff cookie; `AppShell` hides the
   Viva nav from staff). The only way for a staffer to test the student flow is
   to fully log out and re-enter as a student — but there is no signposting for
   this, and after testing they must log back in as staff. The transition is
   undiscoverable.

The backend already supports most of what's needed:

- `POST /api/auth/logout` is **role-agnostic** — it deletes whichever session
  the cookie points at and clears both auth + CSRF cookies. It works for a
  student session today; only the UI to trigger it is missing.
- `GET /api/auth/me` already returns a distinct `{ role: "student", session_id,
  student_id }` shape for student sessions.

So this is mostly a **frontend / UX** change plus small backend additions.

## 2. Goals

- A logged-in student can see they have an active session and can **end it**
  (log out) from any screen.
- Logging out as a student clears the student session cookie and returns them to
  the `/student` entry form.
- Staff can discover the "log out to test as a student" path, and return to staff
  login afterward, without confusion.
- No weakening of existing invariants: staff still cannot sit a viva; one-time
  codes remain single-use; refreshing mid-viva still restores the attempt.

## 3. Non-goals (explicitly out of scope for this spec)

- Persistent student accounts / passwords. Students remain code-authenticated
  per attempt; we are **not** introducing student usernames/passwords.
- Simultaneous staff + student sessions in one browser (dual login). One active
  role per browser profile stays the model.
- SSO / external identity providers.
- Changing the exam-code + roll + one-time-code authentication mechanism itself.

## 4. Current state (reference)

| Concern | Today | File |
| --- | --- | --- |
| Student auth | exam_id + roll + one-time code → `issue_student_session` | `backend/app/main.py:start_session` |
| Student session check | cookie → `require_student_attempt` | `backend/app/main.py` |
| Logout | role-agnostic, clears cookies | `backend/app/main.py:logout` |
| Identity probe | returns staff or student shape | `backend/app/main.py:me` |
| Logout/account UI | **staff only** (`staff && …`) | `frontend/components/AppShell.tsx` |
| Viva nav hidden from staff | `studentOnly` flag | `frontend/components/AppShell.tsx` |

## 5. Proposed design

### 5.1 Student session indicator + logout (frontend)

In `AppShell`:

- When `me.role === "student"`, render a compact session chip in the header:
  - Label: `Student attempt` (optionally the exam name if cheaply available via
    `me` — see 5.4).
  - A **"Leave / End session"** button that calls `logout()` then redirects to
    `/student`.
- Keep the staff branch unchanged. The two are mutually exclusive (one role per
  cookie).
- Confirmation: because ending a session mid-viva is consequential, show a
  confirm dialog ("End your viva session? Your progress is saved; you'll need
  your one-time code to resume."). Resuming is possible only while the one-time
  code/session is still valid — clarify copy accordingly (see edge cases).

### 5.2 Logout semantics (backend — small change)

`logout()` already deletes the session row and clears cookies. Two refinements:

- **Audit**: write an `audit_event` (`student_logout` / `staff_logout`) so we
  retain a record of deliberate session ends. Currently logout is unaudited.
- **CSRF**: logout is a `POST`; confirm the frontend sends the CSRF header
  (the `api()` client does for non-safe methods). No change expected, just a
  test to lock it in.

No change to the role-agnostic behaviour — that is desirable.

### 5.3 Role switching signposting (frontend)

- On the staff side: in the (now staff-only) area, add a subtle "Test as a
  student" affordance that explains they must log out first, with a one-click
  "Log out & go to student entry" action. This avoids staff getting stuck
  wondering why `/student` is hidden.
- On the student entry form (`/student`): if a **staff** cookie is detected
  (the start call would 403), show an inline notice "You're signed in as staff —
  log out to take a viva" with a logout button, instead of a raw 403 banner.

### 5.4 Optional: enrich `me` for students

To show the exam name in the student chip without an extra round-trip, `me` could
include `exam_name` and `attempt_status` for student sessions. Optional; the chip
can ship without it. If added, keep it read-only and non-sensitive (no scores,
no override reasons).

## 6. API surface

| Endpoint | Change | Notes |
| --- | --- | --- |
| `POST /api/auth/logout` | Add audit event; (optional) return `{ ok, role }` | Behaviour otherwise unchanged |
| `GET /api/auth/me` | (Optional) add `exam_name`, `attempt_status` to student shape | Non-sensitive fields only |

No new endpoints are strictly required — the gap is primarily UI.

## 7. UX flows

**Student logout**
1. Student is in `/student` with an active attempt.
2. Clicks "End session" in the header → confirm dialog.
3. Confirm → `POST /api/auth/logout` → cookies cleared → redirect to `/student`
   entry form.
4. Header no longer shows a student chip.

**Staff → student test**
1. Staff clicks "Test as a student" / logs out.
2. `POST /api/auth/logout` → redirect to `/student`.
3. Staff enters a test student's code → takes the viva.
4. Afterward, navigates to staff login and signs back in.

## 8. Edge cases

- **Mid-viva logout**: the attempt row persists (status stays `active`).
  Resuming requires a still-valid session/one-time code. Confirm copy must not
  promise resumption if the one-time code is already consumed and the active
  session was the resume path. (Tie-in: the admin "reset attempt & regenerate
  code" endpoint added in `feat/audit-fixes` is the recovery path.)
- **Shared machine**: logout must clear **both** `twelve_session` and
  `twelve_csrf` cookies (already does) so the next student starts clean.
- **Stale cookie after secret rotation**: out of scope here; covered by the
  existing re-login behaviour.
- **Cross-origin cookie (localhost vs 127.0.0.1)**: out of scope; addressed by
  aligning `NEXT_PUBLIC_API_BASE` to the browsing host.

## 9. Testing (when implemented)

Backend (pytest):
- `logout` on a student session clears the cookie and the session row.
- `logout` writes an audit event with the correct role.
- After student logout, `me` returns 401 and `start` requires a fresh code.

Frontend (manual / future e2e):
- Student chip + End-session button appears only for `role === "student"`.
- End session redirects to `/student` and the form is interactive.
- Staff on `/student` see the "log out to take a viva" notice, not a raw 403.

## 10. Open questions

1. Should ending a student session mid-viva **finalize** the attempt, or leave it
   resumable? (Default proposed: leave resumable; do not auto-finalize.)
2. Should the student chip expose the exam name (requires the optional `me`
   enrichment in 5.4)?
3. Do we want a session-timeout / idle logout for students on shared machines?
   (Possible follow-up spec.)
