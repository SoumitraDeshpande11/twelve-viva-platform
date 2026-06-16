"""Seed a ready-to-take demo viva so you can log in as a student right away.

A fresh database has no exams, so the /student form has nothing to start. This
inserts one published demo exam + one demo student with a known one-time code, then
prints the credentials. Idempotent: re-running replaces the demo exam (FK cascade
clears its old students/sessions) and reprints the same login details.

Run from backend/ with the venv active:
    python seed_demo.py
"""
from __future__ import annotations

import uuid
from pathlib import Path

from dotenv import load_dotenv

# Load the repo-root .env BEFORE importing app modules so the one-time code is hashed
# with the same TWELVE_SECRET_KEY the running server uses (auth.secret_key reads env).
# Without this the seed uses the dev-fallback secret and start_session returns 401.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from app.auth import hash_opaque_token  # noqa: E402
from app.storage import as_json, connect, init_db, utc_now  # noqa: E402

# Stable identifiers so re-seeding cleanly replaces the same demo rather than piling
# up duplicates. The one-time code is what the student types on /student.
EXAM_ID = "demo-exam-0001"
EXAM_NAME = "Demo Viva — TWELVE Platform"
ROLL = "DEMO01"
CODE = "VIVA-DEMO-2026"


def seed() -> None:
    init_db()  # creates + migrates all tables/columns if the DB is new
    now = utc_now()
    with connect() as conn:
        # Replacing the exam cascades (ON DELETE CASCADE) to its students/sessions,
        # so the demo always comes back fresh and immediately startable.
        conn.execute("DELETE FROM exams WHERE id = ?", (EXAM_ID,))
        conn.execute(
            """
            INSERT INTO exams
                (id, name, problem_statement, curriculum, rubric, status, mark_mode, starts_at, ends_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                EXAM_ID,
                EXAM_NAME,
                (
                    "TWELVE is a browser-based AI viva platform: a FastAPI + SQLite backend and a "
                    "Next.js frontend. Admins create an exam from a student CSV, problem statement, "
                    "curriculum, and rubric; students authenticate with an exam code + roll number + "
                    "one-time code and sit a proctored viva where an AI generates questions, "
                    "transcribes spoken answers, and scores them against the rubric; professors review "
                    "transcripts, scores, and proctoring flags and may override marks. Explain how the "
                    "system is designed and why its key invariants hold."
                ),
                (
                    "TWELVE architecture & invariants: cookie + CSRF auth and staff/student roles; "
                    "the AI provider dispatch (auto/openai/gemini/local) and graceful fallback "
                    "(local-fallback in dev, pending_ai_error in prod — never fake a score); "
                    "proctoring flags are review-only and never enter score math; mark modes "
                    "(ai_official vs professor_approved) and score overrides; one-time student codes "
                    "shown once at creation; the transcript hash chain (prev_hash/event_hash); "
                    "server-side transcription with draft fallback; SQLite schema evolution via "
                    "additive migrations."
                ),
                (
                    "Correctness 50% (accurate description of the actual design and invariants), "
                    "depth 50% (trade-offs, failure modes, and why each invariant is enforced)."
                ),
                "published",   # open for student attempts
                "ai_official",  # AI score is official on completion (no professor override needed)
                None,          # no start window
                None,          # no end window
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO students
                (id, exam_id, roll_number, name, email, token, token_hash, token_issued_at, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                EXAM_ID,
                ROLL,
                "Demo Student",
                "demo@example.edu",
                f"invite:{uuid.uuid4()}",        # legacy plaintext column (unused by auth)
                hash_opaque_token(CODE),          # what start_session actually checks
                now,
                as_json({}),
            ),
        )

    print("Demo viva seeded. Log in at http://localhost:3000/student with:")
    print(f"  Exam:          {EXAM_NAME}")
    print(f"  Roll number:   {ROLL}")
    print(f"  One-time code: {CODE}")


if __name__ == "__main__":
    seed()
