from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("TWELVE_DATA_DIR", BASE_DIR / "storage"))
DB_PATH = Path(os.getenv("TWELVE_DB_PATH", DATA_DIR / "twelve.db"))
UPLOAD_DIR = Path(os.getenv("TWELVE_UPLOAD_DIR", DATA_DIR / "uploads"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def as_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def from_json(value: str | None, fallback: Any = None) -> Any:
    if not value:
        return fallback
    return json.loads(value)


@contextmanager
def connect() -> Iterable[sqlite3.Connection]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    # Concurrency hardening: WAL allows concurrent readers with a writer, and
    # busy_timeout makes writers wait for the lock instead of failing immediately
    # with "database is locked" under concurrent vivas. All idempotent per-connect.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS exams (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                problem_statement TEXT NOT NULL,
                curriculum TEXT NOT NULL,
                rubric TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS students (
                id TEXT PRIMARY KEY,
                exam_id TEXT NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
                roll_number TEXT NOT NULL,
                name TEXT NOT NULL,
                email TEXT,
                token TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(exam_id, roll_number),
                UNIQUE(exam_id, token)
            );

            CREATE TABLE IF NOT EXISTS submissions (
                id TEXT PRIMARY KEY,
                exam_id TEXT NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
                student_id TEXT REFERENCES students(id) ON DELETE SET NULL,
                filename TEXT NOT NULL,
                mime_type TEXT,
                storage_path TEXT NOT NULL,
                extracted_text TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS viva_sessions (
                id TEXT PRIMARY KEY,
                exam_id TEXT NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
                student_id TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                permissions_json TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                current_index INTEGER NOT NULL DEFAULT 0,
                final_score REAL,
                started_at TEXT NOT NULL,
                ended_at TEXT
            );

            CREATE TABLE IF NOT EXISTS questions (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES viva_sessions(id) ON DELETE CASCADE,
                ordinal INTEGER NOT NULL,
                category TEXT NOT NULL,
                text TEXT NOT NULL,
                expected_points_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS answers (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES viva_sessions(id) ON DELETE CASCADE,
                question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                input_mode TEXT NOT NULL,
                answer_text TEXT NOT NULL,
                score REAL NOT NULL,
                max_score REAL NOT NULL,
                reasoning TEXT NOT NULL,
                audio_ref TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS transcript_events (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES viva_sessions(id) ON DELETE CASCADE,
                type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS proctoring_events (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES viva_sessions(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                details_json TEXT NOT NULL,
                confidence REAL NOT NULL,
                duration_ms INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS score_reviews (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES viva_sessions(id) ON DELETE CASCADE,
                reviewer TEXT NOT NULL,
                override_score REAL NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_roles (
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                PRIMARY KEY (user_id, role)
            );

            CREATE TABLE IF NOT EXISTS auth_sessions (
                id TEXT PRIMARY KEY,
                token_hash TEXT NOT NULL UNIQUE,
                csrf_token TEXT NOT NULL,
                user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
                student_id TEXT REFERENCES students(id) ON DELETE CASCADE,
                viva_session_id TEXT REFERENCES viva_sessions(id) ON DELETE CASCADE,
                role TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scoring_runs (
                id TEXT PRIMARY KEY,
                answer_id TEXT NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
                status TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT,
                score REAL,
                max_score REAL,
                rubric_breakdown_json TEXT NOT NULL DEFAULT '{}',
                expected_points_covered_json TEXT NOT NULL DEFAULT '[]',
                expected_points_missed_json TEXT NOT NULL DEFAULT '[]',
                concerns_json TEXT NOT NULL DEFAULT '[]',
                reasoning TEXT NOT NULL DEFAULT '',
                prompt_version TEXT NOT NULL DEFAULT 'v1',
                response_hash TEXT NOT NULL DEFAULT '',
                error TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS security_audit_events (
                id TEXT PRIMARY KEY,
                actor_type TEXT NOT NULL,
                actor_id TEXT,
                event_type TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS consents (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES viva_sessions(id) ON DELETE CASCADE,
                consent_text TEXT NOT NULL,
                accepted_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audio_submissions (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES viva_sessions(id) ON DELETE CASCADE,
                question_id TEXT REFERENCES questions(id) ON DELETE SET NULL,
                storage_path TEXT NOT NULL,
                mime_type TEXT,
                size_bytes INTEGER NOT NULL,
                draft_transcript TEXT,
                transcript_text TEXT,
                transcription_status TEXT NOT NULL,
                transcription_provider TEXT NOT NULL,
                transcription_model TEXT,
                transcription_error TEXT,
                created_at TEXT NOT NULL,
                transcribed_at TEXT
            );
            """
        )
        migrate_db(conn)


def migrate_db(conn: sqlite3.Connection) -> None:
    add_column(conn, "exams", "status", "TEXT NOT NULL DEFAULT 'draft'")
    add_column(conn, "exams", "mark_mode", "TEXT NOT NULL DEFAULT 'professor_approved'")
    add_column(conn, "exams", "starts_at", "TEXT")
    add_column(conn, "exams", "ends_at", "TEXT")

    add_column(conn, "students", "token_hash", "TEXT")
    add_column(conn, "students", "token_issued_at", "TEXT")
    add_column(conn, "students", "token_expires_at", "TEXT")
    add_column(conn, "students", "token_used_at", "TEXT")
    add_column(conn, "students", "active_session_id", "TEXT")

    add_column(conn, "viva_sessions", "current_question_id", "TEXT")
    add_column(conn, "viva_sessions", "consent_accepted_at", "TEXT")

    add_column(conn, "answers", "scoring_status", "TEXT NOT NULL DEFAULT 'scored'")
    add_column(conn, "answers", "scorer_provider", "TEXT NOT NULL DEFAULT 'legacy'")
    add_column(conn, "answers", "scorer_error", "TEXT")
    add_column(conn, "answers", "rubric_breakdown_json", "TEXT NOT NULL DEFAULT '{}'")
    add_column(conn, "answers", "expected_points_covered_json", "TEXT NOT NULL DEFAULT '[]'")
    add_column(conn, "answers", "expected_points_missed_json", "TEXT NOT NULL DEFAULT '[]'")
    add_column(conn, "answers", "concerns_json", "TEXT NOT NULL DEFAULT '[]'")
    add_column(conn, "answers", "response_hash", "TEXT NOT NULL DEFAULT ''")
    add_column(conn, "answers", "idempotency_key", "TEXT")

    add_column(conn, "transcript_events", "sequence", "INTEGER")
    add_column(conn, "transcript_events", "prev_hash", "TEXT")
    add_column(conn, "transcript_events", "event_hash", "TEXT")

    add_column(conn, "proctoring_events", "question_id", "TEXT")
    add_column(conn, "proctoring_events", "severity", "TEXT NOT NULL DEFAULT 'warning'")

    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_answers_session_question ON answers(session_id, question_id)")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_answers_session_idempotency ON answers(session_id, idempotency_key) WHERE idempotency_key IS NOT NULL"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_token ON auth_sessions(token_hash)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_transcript_session_sequence ON transcript_events(session_id, sequence)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audio_submissions_session ON audio_submissions(session_id, created_at)")

    # Full-viva webcam recordings for examiner review (review-only, never scored).
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS session_recordings (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES viva_sessions(id) ON DELETE CASCADE,
            storage_path TEXT NOT NULL,
            mime_type TEXT,
            size_bytes INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_session_recordings_session ON session_recordings(session_id, created_at)")


def add_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]
