from __future__ import annotations

import os
import secrets
import hashlib
import hmac
import shutil
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# Load repo-root .env before any os.getenv() below; uvicorn does not load it on its own.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from .agent import build_question_plan, create_followup, score_answer
from .auth import (
    AUTH_COOKIE,
    CSRF_COOKIE,
    SAFE_METHODS,
    STAFF_SESSION_HOURS,
    STUDENT_SESSION_HOURS,
    expires_at,
    generate_token,
    hash_opaque_token,
    hash_password,
    is_expired,
    secret_key,
    session_cookie_options,
    verify_password,
)
from .file_processing import extract_text_from_upload, parse_students_csv
from .gemini_agent import (
    GeminiAgentError,
    build_question_plan_with_gemini,
    create_followup_with_gemini,
    gemini_configured,
    gemini_health_check,
    score_answer_with_gemini,
)
from .gemini_voice import GeminiVoiceError, create_live_ephemeral_token, gemini_tts_configured, synthesize_question_wav
from .ollama_agent import (
    OllamaAgentError,
    build_question_plan_with_ollama,
    create_followup_with_ollama,
    ollama_configured,
    ollama_health_check,
    ollama_model,
    score_answer_with_ollama,
)
from .openai_agent import (
    OpenAIAgentError,
    build_question_plan_with_openai,
    create_followup_with_openai,
    openai_configured,
    score_answer_with_openai,
)
from .storage import UPLOAD_DIR, as_json, connect, from_json, init_db, row_to_dict, rows_to_dicts, utc_now
from .transcription import TranscriptionError, transcription_provider, transcribe_audio


app = FastAPI(title="TWELVE Pilot API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("TWELVE_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Permissions(BaseModel):
    camera: bool = False
    microphone: bool = False
    fullscreen: bool = False
    screen: bool = False


class StartSessionRequest(BaseModel):
    exam_id: str
    roll_number: str
    one_time_code: str
    permissions: Permissions
    consent_text: str = "I consent to camera, microphone, screen, transcript, audio, score, and proctoring flag processing for this viva."


class AnswerRequest(BaseModel):
    question_id: str
    answer_text: str = ""
    input_mode: str = Field(pattern="^(voice|typed)$")
    audio_ref: str | None = None


class ProctoringEventRequest(BaseModel):
    event_type: str
    details: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0
    duration_ms: int | None = None
    severity: str = Field(default="warning", pattern="^(info|warning|high)$")


class ReviewRequest(BaseModel):
    reviewer: str
    override_score: float
    reason: str


STAFF_ROLES = {"super_admin", "exam_admin", "examiner", "invigilator"}


class LoginRequest(BaseModel):
    email: str
    password: str


class BootstrapRequest(BaseModel):
    email: str
    name: str
    password: str
    bootstrap_token: str | None = None


class CreateStaffRequest(BaseModel):
    email: str
    name: str
    password: str
    roles: list[str]


class UpdateStaffRequest(BaseModel):
    """Edit an existing staff member. Omitted fields are left unchanged."""
    roles: list[str] | None = None
    active: bool | None = None


class AssignStaffRequest(BaseModel):
    user_id: str


class AudioRefRequest(BaseModel):
    audio_ref: str


class LiveTurnEventRequest(BaseModel):
    event_type: str = Field(pattern="^(question_displayed|tts_started|tts_completed|recording_started|recording_stopped|server_transcript_received|answer_submit_started|answer_submit_completed|answer_submit_failed)$")
    question_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


@app.on_event("startup")
def startup() -> None:
    init_db()
    normalize_legacy_data()
    ensure_env_bootstrap_admin()


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "twelve-api",
        "ai_provider": selected_ai_provider(),
        "openai_configured": openai_configured(),
        "gemini_configured": gemini_configured(),
        "gemini_tts_configured": gemini_tts_configured(),
        "transcription_provider": transcription_provider(),
        "ollama_model": ollama_model() if selected_ai_provider() == "ollama" else None,
    }


@app.post("/api/auth/bootstrap")
def bootstrap_admin(payload: BootstrapRequest, response: Response) -> dict[str, Any]:
    # The first-account endpoint is unauthenticated, so it must be gated or any
    # visitor could claim super_admin. Require a server-side secret token when one
    # is configured; outside local/dev/test, refuse entirely if no token is set.
    expected_token = os.getenv("TWELVE_BOOTSTRAP_TOKEN")
    if expected_token:
        if not payload.bootstrap_token or not secrets.compare_digest(payload.bootstrap_token, expected_token):
            raise HTTPException(status_code=403, detail="Invalid or missing bootstrap token.")
    elif not local_ai_allowed():
        raise HTTPException(
            status_code=403,
            detail="Bootstrap is disabled. Set TWELVE_BOOTSTRAP_TOKEN (or TWELVE_BOOTSTRAP_ADMIN_*) to create the first account.",
        )
    with connect() as conn:
        existing = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if existing:
            raise HTTPException(status_code=409, detail="Bootstrap is disabled after the first staff account exists.")
        user_id = create_staff_user(conn, payload.email, payload.name, payload.password, ["super_admin", "exam_admin", "examiner", "invigilator"])
        audit_event(conn, "staff", user_id, "staff_bootstrapped", {"email": payload.email})
        return issue_staff_session(conn, response, user_id)


@app.get("/api/auth/staff")
def list_staff(request: Request) -> list[dict[str, Any]]:
    """Directory of all staff accounts with their roles. super_admin only."""
    require_staff(request, {"super_admin"})
    with connect() as conn:
        users = rows_to_dicts(conn.execute("SELECT id, email, name, active, created_at FROM users ORDER BY created_at"))
        for user in users:
            user["roles"] = sorted(row["role"] for row in conn.execute("SELECT role FROM user_roles WHERE user_id = ?", (user["id"],)))
            user["active"] = bool(user["active"])
    return users


@app.post("/api/auth/staff")
def create_staff(payload: CreateStaffRequest, request: Request) -> dict[str, Any]:
    """Create a brand-new staff account. super_admin only (CSRF enforced)."""
    auth = require_staff(request, {"super_admin"})
    roles = [role for role in payload.roles if role in STAFF_ROLES]
    if not roles:
        raise HTTPException(status_code=400, detail="At least one valid role is required.")
    with connect() as conn:
        if conn.execute("SELECT 1 FROM users WHERE lower(email) = lower(?)", (payload.email,)).fetchone():
            raise HTTPException(status_code=409, detail="A user with that email already exists.")
        user_id = create_staff_user(conn, payload.email, payload.name, payload.password, roles)
        audit_event(conn, "staff", auth["user_id"], "staff_created", {"email": payload.email, "roles": roles})
    return {"id": user_id, "email": payload.email.strip().lower(), "name": payload.name.strip(), "roles": roles, "active": True}


@app.patch("/api/auth/staff/{user_id}")
def update_staff(user_id: str, payload: UpdateStaffRequest, request: Request) -> dict[str, Any]:
    """Edit an existing staff member's roles or active status. super_admin only.

    Guards against locking everyone out: the last active super_admin cannot be demoted or
    deactivated, and a staffer cannot deactivate their own account (self-lockout)."""
    auth = require_staff(request, {"super_admin"})
    with connect() as conn:
        existing = row_to_dict(conn.execute("SELECT id, email, name, active FROM users WHERE id = ?", (user_id,)).fetchone())
        if not existing:
            raise HTTPException(status_code=404, detail="Staff member not found.")
        current_roles = {row["role"] for row in conn.execute("SELECT role FROM user_roles WHERE user_id = ?", (user_id,))}
        new_roles = current_roles
        if payload.roles is not None:
            new_roles = {role for role in payload.roles if role in STAFF_ROLES}
            if not new_roles:
                raise HTTPException(status_code=400, detail="At least one valid role is required.")
        new_active = existing["active"] if payload.active is None else (1 if payload.active else 0)

        if user_id == auth["user_id"] and new_active == 0:
            raise HTTPException(status_code=400, detail="You cannot deactivate your own account.")

        # Protect the last remaining active super_admin from being demoted/deactivated.
        active_super_admins = {
            row["user_id"]
            for row in conn.execute(
                "SELECT ur.user_id FROM user_roles ur JOIN users u ON u.id = ur.user_id WHERE ur.role = 'super_admin' AND u.active = 1"
            )
        }
        was_active_super_admin = "super_admin" in current_roles and existing["active"] == 1
        loses_super_admin = was_active_super_admin and ("super_admin" not in new_roles or new_active == 0)
        if loses_super_admin and active_super_admins <= {user_id}:
            raise HTTPException(status_code=400, detail="At least one active super admin must remain.")

        if payload.roles is not None and new_roles != current_roles:
            conn.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
            for role in sorted(new_roles):
                conn.execute("INSERT INTO user_roles (user_id, role) VALUES (?, ?)", (user_id, role))
        if new_active != existing["active"]:
            conn.execute("UPDATE users SET active = ? WHERE id = ?", (new_active, user_id))
            if new_active == 0:
                # Revoke any live sessions for a deactivated account so access ends immediately.
                conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
        audit_event(
            conn, "staff", auth["user_id"], "staff_updated",
            {"user_id": user_id, "roles": sorted(new_roles), "active": bool(new_active)},
        )
    return {"id": user_id, "email": existing["email"], "name": existing["name"], "roles": sorted(new_roles), "active": bool(new_active)}


@app.post("/api/auth/login")
def login(payload: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    email_key = f"email:{payload.email.strip().lower()}"
    ip_key = f"ip:{client_ip(request)}"
    with connect() as conn:
        enforce_login_rate_limit(conn, email_key, ip_key)
        user = row_to_dict(conn.execute("SELECT * FROM users WHERE lower(email) = lower(?) AND active = 1", (payload.email,)).fetchone())
        login_ok = bool(user) and verify_password(user["password_hash"], payload.password)
        if not login_ok:
            # Record the failed attempt against both the email and the source IP so a
            # later request can be locked out; never reveal which factor was wrong.
            # Done in its own committed transaction because we raise (which would otherwise
            # roll back this connection and lose the attempt record).
            record_failed_login(email_key, ip_key, payload.email.strip().lower(), client_ip(request))
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        # Successful login clears the throttle window for this email and IP.
        conn.execute(
            "DELETE FROM security_audit_events WHERE event_type = 'staff_login_failed' AND actor_id IN (?, ?)",
            (email_key, ip_key),
        )
        audit_event(conn, "staff", user["id"], "staff_login", {"email": user["email"]})
        return issue_staff_session(conn, response, user["id"])


def record_failed_login(email_key: str, ip_key: str, email: str, ip: str) -> None:
    with connect() as conn:
        audit_event(conn, "staff", email_key, "staff_login_failed", {"email": email, "ip": ip})
        audit_event(conn, "staff", ip_key, "staff_login_failed", {"email": email, "ip": ip})


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def enforce_login_rate_limit(conn: Any, email_key: str, ip_key: str) -> None:
    """Lock out staff login after too many recent failures, keyed by email AND source IP.

    Reuses the existing security_audit_events table (same pattern as the provider-token
    throttle). A 429 is raised before any password check so attempts during a lockout do
    not extend it further beyond the recorded failures.
    """
    window_minutes = int(os.getenv("TWELVE_LOGIN_LOCKOUT_WINDOW_MINUTES", "15"))
    max_attempts = int(os.getenv("TWELVE_LOGIN_MAX_ATTEMPTS", "5"))
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=window_minutes)).isoformat()
    for key in (email_key, ip_key):
        failures = conn.execute(
            "SELECT COUNT(*) FROM security_audit_events WHERE event_type = 'staff_login_failed' AND actor_id = ? AND created_at >= ?",
            (key, cutoff),
        ).fetchone()[0]
        if failures >= max_attempts:
            raise HTTPException(status_code=429, detail="Too many failed login attempts. Try again later.")


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict[str, Any]:
    token = request.cookies.get(AUTH_COOKIE)
    role: str | None = None
    with connect() as conn:
        if token:
            session = row_to_dict(
                conn.execute("SELECT * FROM auth_sessions WHERE token_hash = ?", (hash_opaque_token(token),)).fetchone()
            )
            if session:
                role = session["role"]
                # Record deliberate session ends so we retain an audit trail of who
                # logged out (and when) — important on shared/lab machines. Role-agnostic
                # behaviour is preserved; this only adds the event.
                actor_id = session.get("student_id") if role == "student" else session.get("user_id")
                audit_event(conn, role, actor_id, f"{role}_logout", {"session_id": session["id"]})
            conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (hash_opaque_token(token),))
    response.delete_cookie(AUTH_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True, "role": role}


@app.get("/api/auth/me")
def me(request: Request) -> dict[str, Any]:
    auth = get_auth_session(request, require_csrf=False)
    if auth["role"] == "student":
        return {
            "role": "student",
            "csrf_token": auth["csrf_token"],
            "session_id": auth["viva_session_id"],
            "student_id": auth["student_id"],
        }
    with connect() as conn:
        user = row_to_dict(conn.execute("SELECT id, email, name FROM users WHERE id = ?", (auth["user_id"],)).fetchone())
        roles = [row["role"] for row in conn.execute("SELECT role FROM user_roles WHERE user_id = ?", (auth["user_id"],))]
    return {"role": "staff", "user": user, "roles": roles, "csrf_token": auth["csrf_token"]}


def normalize_window_bound(value: str | None, field: str) -> datetime | None:
    """Parse an optional exam-window timestamp (offset-aware ISO) to a tz-aware datetime, or None.

    A naive value (no offset) is assumed UTC; clients should send an offset-aware
    ISO string (e.g. new Date(localInput).toISOString()) so the admin's local time
    is not silently reinterpreted as UTC.
    """
    if not value or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {field} timestamp.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


@app.post("/api/admin/exams")
async def create_exam(
    request: Request,
    name: str = Form(...),
    problem_statement: str = Form(...),
    curriculum: str = Form(...),
    rubric: str = Form(...),
    mark_mode: str = Form("professor_approved"),
    starts_at: str = Form(""),
    ends_at: str = Form(""),
    student_csv: UploadFile = File(...),
    submissions: list[UploadFile] = File(default=[]),
) -> dict[str, Any]:
    require_staff(request, {"super_admin", "exam_admin"})
    if mark_mode not in {"professor_approved", "ai_official"}:
        raise HTTPException(status_code=400, detail="Unsupported mark mode.")
    starts_at_dt = normalize_window_bound(starts_at, "starts_at")
    ends_at_dt = normalize_window_bound(ends_at, "ends_at")
    if starts_at_dt and ends_at_dt and ends_at_dt <= starts_at_dt:
        raise HTTPException(status_code=400, detail="Exam end time must be after the start time.")
    starts_at_value = starts_at_dt.isoformat() if starts_at_dt else None
    ends_at_value = ends_at_dt.isoformat() if ends_at_dt else None
    exam_id = str(uuid.uuid4())
    now = utc_now()
    max_upload_bytes = int(os.getenv("TWELVE_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
    csv_bytes = await student_csv.read()
    if len(csv_bytes) > max_upload_bytes:
        raise HTTPException(status_code=413, detail="Student CSV upload is too large.")
    students = parse_students_csv(csv_bytes)
    if not students:
        raise HTTPException(status_code=400, detail="Student CSV did not contain any rows.")
    raw_row_count = csv_row_count(csv_bytes)
    skipped_rows = max(raw_row_count - len(students), 0)
    warnings: list[str] = []
    if skipped_rows:
        warnings.append(f"{skipped_rows} CSV row(s) were skipped (e.g. missing roll_number or malformed).")

    exam_dir = UPLOAD_DIR / exam_id
    exam_dir.mkdir(parents=True, exist_ok=True)
    issued_tokens: dict[str, str] = {}

    with connect() as conn:
        conn.execute(
            "INSERT INTO exams (id, name, problem_statement, curriculum, rubric, status, mark_mode, starts_at, ends_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (exam_id, name, problem_statement, curriculum, rubric, "published", mark_mode, starts_at_value, ends_at_value, now),
        )
        student_ids: dict[str, str] = {}
        for student in students:
            student_id = str(uuid.uuid4())
            token = generate_token(24)
            issued_tokens[student_id] = token
            student_ids[student["roll_number"].lower()] = student_id
            conn.execute(
                """
                INSERT INTO students (id, exam_id, roll_number, name, email, token, token_hash, token_issued_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    student_id,
                    exam_id,
                    student["roll_number"],
                    student["name"],
                    student["email"],
                    f"invite:{uuid.uuid4()}",
                    hash_opaque_token(token),
                    now,
                    as_json(student["metadata"]),
                ),
            )

        for upload in submissions:
            content = await upload.read()
            if len(content) > max_upload_bytes:
                raise HTTPException(status_code=413, detail=f"Submission upload '{upload.filename or 'file'}' is too large.")
            filename = Path(upload.filename or "submission.bin").name
            storage_name = f"{uuid.uuid4()}-{filename}"
            storage_path = exam_dir / storage_name
            storage_path.write_bytes(content)
            student_id = infer_student_id(filename, student_ids)
            conn.execute(
                """
                INSERT INTO submissions (id, exam_id, student_id, filename, mime_type, storage_path, extracted_text, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    exam_id,
                    student_id,
                    filename,
                    upload.content_type,
                    str(storage_path),
                    extract_text_from_upload(filename, content),
                    now,
                ),
            )
        audit_event(conn, "staff", get_auth_session(request, require_csrf=False)["user_id"], "exam_created", {"exam_id": exam_id, "students": len(students)})

    exam = get_exam_for_response(exam_id)
    for student in exam["students"]:
        student["token"] = issued_tokens.get(student["id"])
    exam["skipped_rows"] = skipped_rows
    exam["warnings"] = warnings
    return exam


@app.get("/api/admin/exams")
def list_exams(request: Request, include_archived: bool = False) -> list[dict[str, Any]]:
    require_staff(request, {"super_admin", "exam_admin", "examiner", "invigilator"})
    with connect() as conn:
        exams = rows_to_dicts(conn.execute("SELECT * FROM exams ORDER BY created_at DESC"))
        out: list[dict[str, Any]] = []
        for exam in exams:
            exam["archived"] = bool(exam.get("archived_at"))
            if exam["archived"] and not include_archived:
                continue
            exam["student_count"] = conn.execute("SELECT COUNT(*) FROM students WHERE exam_id = ?", (exam["id"],)).fetchone()[0]
            exam["session_count"] = conn.execute("SELECT COUNT(*) FROM viva_sessions WHERE exam_id = ?", (exam["id"],)).fetchone()[0]
            out.append(exam)
        return out


@app.get("/api/admin/exams/{exam_id}")
def get_exam(exam_id: str, request: Request) -> dict[str, Any]:
    require_staff(request, {"super_admin", "exam_admin", "examiner", "invigilator"})
    return get_exam_for_response(exam_id)


class UpdateExamRequest(BaseModel):
    """Partial update of an exam's definition. Omitted fields are left unchanged.
    Roster (students) and submissions are NOT edited here — those have their own flows."""
    name: str | None = None
    problem_statement: str | None = None
    curriculum: str | None = None
    rubric: str | None = None
    mark_mode: str | None = None
    # Empty string clears the bound; a datetime-local string sets it.
    starts_at: str | None = None
    ends_at: str | None = None


@app.patch("/api/admin/exams/{exam_id}")
def update_exam(exam_id: str, payload: UpdateExamRequest, request: Request) -> dict[str, Any]:
    """Edit an existing exam's text/config fields. Does not retro-change already-scored
    answers (final score is answer-scores only) — new wording affects future vivas."""
    auth = require_staff(request, {"super_admin", "exam_admin"})
    with connect() as conn:
        existing = row_to_dict(conn.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone())
        if not existing:
            raise HTTPException(status_code=404, detail="Exam not found")

        updates: dict[str, Any] = {}
        for field in ("name", "problem_statement", "curriculum", "rubric"):
            value = getattr(payload, field)
            if value is not None:
                text = value.strip()
                if not text:
                    raise HTTPException(status_code=400, detail=f"{field.replace('_', ' ').capitalize()} cannot be empty.")
                updates[field] = text

        if payload.mark_mode is not None:
            if payload.mark_mode not in {"professor_approved", "ai_official"}:
                raise HTTPException(status_code=400, detail="Unsupported mark mode.")
            updates["mark_mode"] = payload.mark_mode

        # Window bounds: an explicit empty string clears the bound; otherwise normalize it.
        starts_value = existing.get("starts_at")
        ends_value = existing.get("ends_at")
        if payload.starts_at is not None:
            dt = normalize_window_bound(payload.starts_at, "starts_at") if payload.starts_at.strip() else None
            starts_value = dt.isoformat() if dt else None
            updates["starts_at"] = starts_value
        if payload.ends_at is not None:
            dt = normalize_window_bound(payload.ends_at, "ends_at") if payload.ends_at.strip() else None
            ends_value = dt.isoformat() if dt else None
            updates["ends_at"] = ends_value
        if starts_value and ends_value and datetime.fromisoformat(ends_value) <= datetime.fromisoformat(starts_value):
            raise HTTPException(status_code=400, detail="Exam end time must be after the start time.")

        if updates:
            set_clause = ", ".join(f"{key} = ?" for key in updates)
            conn.execute(f"UPDATE exams SET {set_clause} WHERE id = ?", (*updates.values(), exam_id))
            audit_event(conn, "staff", auth["user_id"], "exam_updated", {"exam_id": exam_id, "fields": list(updates.keys())})
    return get_exam_for_response(exam_id)


# --- Exam access scoping (assigned-staff model) ------------------------------
ADMIN_ROLES = {"super_admin", "exam_admin"}


def accessible_exam_ids(auth: dict[str, Any]) -> set[str] | None:
    """Exam ids a staff user may access. None = all (admins). Examiners/invigilators are
    limited to exams explicitly assigned to them."""
    if set(auth.get("roles", [])) & ADMIN_ROLES:
        return None
    with connect() as conn:
        return {row["exam_id"] for row in conn.execute("SELECT exam_id FROM exam_assignments WHERE user_id = ?", (auth["user_id"],))}


def require_exam_access(auth: dict[str, Any], exam_id: str) -> None:
    scope = accessible_exam_ids(auth)
    if scope is not None and exam_id not in scope:
        raise HTTPException(status_code=403, detail="You are not assigned to this exam.")


@app.post("/api/admin/exams/{exam_id}/archive")
def archive_exam(exam_id: str, request: Request) -> dict[str, Any]:
    """File a finished exam away: hidden from default lists but retained and retrievable."""
    auth = require_staff(request, ADMIN_ROLES)
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM exams WHERE id = ?", (exam_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Exam not found")
        conn.execute("UPDATE exams SET archived_at = ? WHERE id = ? AND archived_at IS NULL", (utc_now(), exam_id))
        audit_event(conn, "staff", auth["user_id"], "exam_archived", {"exam_id": exam_id})
    return get_exam_for_response(exam_id)


@app.post("/api/admin/exams/{exam_id}/unarchive")
def unarchive_exam(exam_id: str, request: Request) -> dict[str, Any]:
    """Restore an archived exam back into the active lists."""
    auth = require_staff(request, ADMIN_ROLES)
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM exams WHERE id = ?", (exam_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Exam not found")
        conn.execute("UPDATE exams SET archived_at = NULL WHERE id = ?", (exam_id,))
        audit_event(conn, "staff", auth["user_id"], "exam_unarchived", {"exam_id": exam_id})
    return get_exam_for_response(exam_id)


@app.get("/api/admin/assignable-staff")
def assignable_staff(request: Request) -> list[dict[str, Any]]:
    """Active staff that can be assigned to an exam. Available to exam managers."""
    require_staff(request, ADMIN_ROLES)
    with connect() as conn:
        users = rows_to_dicts(conn.execute("SELECT id, email, name FROM users WHERE active = 1 ORDER BY name"))
        for user in users:
            user["roles"] = sorted(row["role"] for row in conn.execute("SELECT role FROM user_roles WHERE user_id = ?", (user["id"],)))
    return users


@app.get("/api/admin/exams/{exam_id}/assignments")
def list_exam_assignments(exam_id: str, request: Request) -> list[dict[str, Any]]:
    """Staff currently assigned to an exam."""
    require_staff(request, ADMIN_ROLES)
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM exams WHERE id = ?", (exam_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Exam not found")
        rows = rows_to_dicts(
            conn.execute(
                """
                SELECT u.id, u.email, u.name, ea.created_at
                FROM exam_assignments ea JOIN users u ON u.id = ea.user_id
                WHERE ea.exam_id = ? ORDER BY u.name
                """,
                (exam_id,),
            )
        )
        for row in rows:
            row["roles"] = sorted(r["role"] for r in conn.execute("SELECT role FROM user_roles WHERE user_id = ?", (row["id"],)))
    return rows


@app.post("/api/admin/exams/{exam_id}/assignments")
def assign_staff_to_exam(exam_id: str, payload: AssignStaffRequest, request: Request) -> list[dict[str, Any]]:
    """Assign an existing staff member to an exam (idempotent)."""
    auth = require_staff(request, ADMIN_ROLES)
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM exams WHERE id = ?", (exam_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Exam not found")
        if not conn.execute("SELECT 1 FROM users WHERE id = ? AND active = 1", (payload.user_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Staff member not found.")
        conn.execute(
            "INSERT OR IGNORE INTO exam_assignments (exam_id, user_id, assigned_by, created_at) VALUES (?, ?, ?, ?)",
            (exam_id, payload.user_id, auth["user_id"], utc_now()),
        )
        audit_event(conn, "staff", auth["user_id"], "exam_staff_assigned", {"exam_id": exam_id, "user_id": payload.user_id})
    return list_exam_assignments(exam_id, request)


@app.delete("/api/admin/exams/{exam_id}/assignments/{user_id}")
def unassign_staff_from_exam(exam_id: str, user_id: str, request: Request) -> list[dict[str, Any]]:
    """Remove a staff member's assignment from an exam."""
    auth = require_staff(request, ADMIN_ROLES)
    with connect() as conn:
        conn.execute("DELETE FROM exam_assignments WHERE exam_id = ? AND user_id = ?", (exam_id, user_id))
        audit_event(conn, "staff", auth["user_id"], "exam_staff_unassigned", {"exam_id": exam_id, "user_id": user_id})
    return list_exam_assignments(exam_id, request)


@app.delete("/api/admin/exams/{exam_id}", status_code=204)
def delete_exam(exam_id: str, request: Request) -> Response:
    auth = require_staff(request, {"super_admin", "exam_admin"})
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM exams WHERE id = ?", (exam_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Exam not found")
        # Collect session ids first so we can remove their on-disk media after the cascade.
        session_ids = [row["id"] for row in conn.execute("SELECT id FROM viva_sessions WHERE exam_id = ?", (exam_id,))]
        # FK ON DELETE CASCADE (PRAGMA foreign_keys=ON in storage.connect) removes the
        # exam's students, sessions, answers, questions, transcript/proctoring/audio/recording rows.
        conn.execute("DELETE FROM exams WHERE id = ?", (exam_id,))
        audit_event(conn, "staff", auth["user_id"], "exam_deleted", {"exam_id": exam_id})
    # The DB rows cascade, but the recorded files do not — remove the per-session dirs.
    for session_id in session_ids:
        shutil.rmtree(UPLOAD_DIR / "recordings" / session_id, ignore_errors=True)
        shutil.rmtree(UPLOAD_DIR / "audio" / session_id, ignore_errors=True)
    return Response(status_code=204)


@app.post("/api/admin/exams/{exam_id}/students/{student_id}/reset-attempt")
def reset_student_attempt(exam_id: str, student_id: str, request: Request) -> dict[str, Any]:
    auth = require_staff(request, {"super_admin", "exam_admin"})
    now = utc_now()
    new_token = generate_token(24)
    with connect() as conn:
        student = row_to_dict(
            conn.execute("SELECT * FROM students WHERE id = ? AND exam_id = ?", (student_id, exam_id)).fetchone()
        )
        if not student:
            raise HTTPException(status_code=404, detail="Student not found for this exam.")
        # Invalidate any existing attempt: mark active/used sessions reset and drop their auth sessions.
        sessions = rows_to_dicts(
            conn.execute("SELECT id FROM viva_sessions WHERE student_id = ? AND exam_id = ?", (student_id, exam_id))
        )
        for session in sessions:
            conn.execute(
                "UPDATE viva_sessions SET status = ?, ended_at = COALESCE(ended_at, ?) WHERE id = ? AND status = 'active'",
                ("reset", now, session["id"]),
            )
            conn.execute("DELETE FROM auth_sessions WHERE viva_session_id = ?", (session["id"],))
        # Regenerate a fresh one-time code and clear the used/active markers so the student can restart.
        conn.execute(
            "UPDATE students SET token_hash = ?, token_issued_at = ?, token_used_at = NULL, active_session_id = NULL WHERE id = ?",
            (hash_opaque_token(new_token), now, student_id),
        )
        audit_event(conn, "staff", auth["user_id"], "student_attempt_reset", {"exam_id": exam_id, "student_id": student_id})
    return {"student_id": student_id, "roll_number": student["roll_number"], "token": new_token}


def get_exam_for_response(exam_id: str) -> dict[str, Any]:
    with connect() as conn:
        exam = row_to_dict(conn.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone())
        if not exam:
            raise HTTPException(status_code=404, detail="Exam not found")
        exam["archived"] = bool(exam.get("archived_at"))
        exam["students"] = rows_to_dicts(
            conn.execute("SELECT id, roll_number, name, email, NULL AS token FROM students WHERE exam_id = ? ORDER BY roll_number", (exam_id,))
        )
        exam["submissions"] = rows_to_dicts(
            conn.execute("SELECT id, student_id, filename, mime_type, created_at FROM submissions WHERE exam_id = ? ORDER BY created_at", (exam_id,))
        )
        return exam


@app.get("/api/public/exams")
def public_exams() -> list[dict[str, Any]]:
    with connect() as conn:
        return rows_to_dicts(conn.execute("SELECT id, name, status FROM exams WHERE status IN ('published', 'open') ORDER BY created_at DESC"))


@app.get("/api/public/exams/{exam_id}/landing")
def public_exam_landing(exam_id: str) -> dict[str, Any]:
    with connect() as conn:
        exam = row_to_dict(conn.execute("SELECT id, name, status FROM exams WHERE id = ?", (exam_id,)).fetchone())
    if not exam:
        raise HTTPException(status_code=404, detail="Exam not found")
    return exam


@app.post("/api/student/attempts/start")
def start_session(payload: StartSessionRequest, request: Request, response: Response) -> dict[str, Any]:
    # Staff must not sit a viva (they create exams and know the codes — conflict of
    # interest). Reject if a staff session cookie is present; they must log out first.
    staff_token = request.cookies.get(AUTH_COOKIE)
    if staff_token:
        with connect() as conn:
            existing = row_to_dict(
                conn.execute("SELECT role, expires_at FROM auth_sessions WHERE token_hash = ?", (hash_opaque_token(staff_token),)).fetchone()
            )
        if existing and existing.get("role") == "staff" and not is_expired(existing["expires_at"]):
            raise HTTPException(status_code=403, detail="Staff accounts cannot take a viva. Log out first.")

    if not (payload.permissions.camera and payload.permissions.microphone and payload.permissions.fullscreen):
        raise HTTPException(status_code=403, detail="Camera, microphone, and fullscreen permissions are required before starting.")

    with connect() as conn:
        exam = row_to_dict(conn.execute("SELECT * FROM exams WHERE id = ?", (payload.exam_id,)).fetchone())
        if not exam:
            raise HTTPException(status_code=404, detail="Exam not found")
        if exam.get("status") not in {"published", "open"}:
            raise HTTPException(status_code=403, detail="Exam is not open for student attempts.")
        student = row_to_dict(
            conn.execute(
                """
                SELECT * FROM students
                WHERE exam_id = ? AND lower(roll_number) = lower(?)
                """,
                (payload.exam_id, payload.roll_number),
            ).fetchone()
        )
        if not student or not student.get("token_hash") or not secrets.compare_digest(student["token_hash"], hash_opaque_token(payload.one_time_code)):
            raise HTTPException(status_code=401, detail="Invalid roll number or one-time code.")
        if student.get("token_expires_at") and is_expired(student["token_expires_at"]):
            raise HTTPException(status_code=403, detail="This one-time code has expired.")

        active_session = row_to_dict(
            conn.execute(
                """
                SELECT * FROM viva_sessions
                WHERE student_id = ? AND exam_id = ? AND status = 'active'
                ORDER BY started_at DESC LIMIT 1
                """,
                (student["id"], payload.exam_id),
            ).fetchone()
        )
        if active_session:
            session_auth = issue_student_session(conn, response, student["id"], active_session["id"])
            data = hydrate_session(conn, active_session["id"])
            data["csrf_token"] = session_auth["csrf_token"]
            return data
        if student.get("token_used_at"):
            raise HTTPException(status_code=409, detail="This one-time code has already been used. Ask staff to reset the attempt.")
        # Exam time window: only enforced for fresh starts; an already-active attempt resumes above.
        if exam.get("starts_at") and not is_expired(exam["starts_at"]):
            raise HTTPException(status_code=403, detail="This exam has not opened yet.")
        if exam.get("ends_at") and is_expired(exam["ends_at"]):
            raise HTTPException(status_code=403, detail="This exam window has closed.")

        submission_text = "\n".join(
            row["extracted_text"]
            for row in conn.execute(
                "SELECT extracted_text FROM submissions WHERE exam_id = ? AND (student_id = ? OR student_id IS NULL)",
                (payload.exam_id, student["id"]),
            )
        )
        plan, agent_provider, agent_error = make_question_plan(exam, student, submission_text)
        note_provider_outcome(agent_provider, agent_error)
        session_id = str(uuid.uuid4())
        now = utc_now()
        conn.execute(
            """
            INSERT INTO viva_sessions (id, exam_id, student_id, status, permissions_json, plan_json, current_index, started_at, consent_accepted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, payload.exam_id, student["id"], "active", as_json(payload.permissions.model_dump()), as_json([q.__dict__ for q in plan]), 0, now, now),
        )
        for ordinal, seed in enumerate(plan, start=1):
            conn.execute(
                """
                INSERT INTO questions (id, session_id, ordinal, category, text, expected_points_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), session_id, ordinal, seed.category, seed.text, as_json(seed.expected_points), now),
        )
        log_transcript(
            conn,
            session_id,
            "session_started",
            {"permissions": payload.permissions.model_dump(), "agent_provider": agent_provider, "agent_error": agent_error},
        )
        first_question = row_to_dict(conn.execute("SELECT id FROM questions WHERE session_id = ? ORDER BY ordinal LIMIT 1", (session_id,)).fetchone())
        conn.execute("UPDATE viva_sessions SET current_question_id = ? WHERE id = ?", (first_question["id"] if first_question else None, session_id))
        conn.execute("UPDATE students SET token_used_at = ?, active_session_id = ? WHERE id = ?", (now, session_id, student["id"]))
        conn.execute(
            "INSERT INTO consents (id, session_id, consent_text, accepted_at) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), session_id, payload.consent_text, now),
        )
        session_auth = issue_student_session(conn, response, student["id"], session_id)
        data = hydrate_session(conn, session_id)
        data["csrf_token"] = session_auth["csrf_token"]
        return data


@app.post("/api/sessions/start")
def deprecated_start_session(payload: StartSessionRequest, request: Request, response: Response) -> dict[str, Any]:
    return start_session(payload, request, response)


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str, request: Request) -> dict[str, Any]:
    require_session_access(request, session_id, require_csrf=False)
    with connect() as conn:
        return hydrate_session(conn, session_id)


@app.get("/api/student/attempts/current")
def current_attempt(request: Request) -> dict[str, Any]:
    auth = require_student_attempt(request)
    with connect() as conn:
        return hydrate_session(conn, auth["viva_session_id"])


@app.get("/api/sessions/{session_id}/questions/{question_id}/tts")
def question_tts(session_id: str, question_id: str, request: Request) -> FileResponse:
    require_session_access(request, session_id, require_csrf=False)
    with connect() as conn:
        question = row_to_dict(conn.execute("SELECT * FROM questions WHERE id = ? AND session_id = ?", (question_id, session_id)).fetchone())
        if not question:
            raise HTTPException(status_code=404, detail="Question not found")

    model = os.getenv("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview")
    voice = os.getenv("GEMINI_TTS_VOICE", "Kore")
    text_hash = hashlib.sha256(question["text"].encode("utf-8")).hexdigest()[:16]
    audio_path = UPLOAD_DIR / "tts" / session_id / f"{question_id}-{model}-{voice}-{text_hash}.wav"
    if not audio_path.exists():
        try:
            synthesize_question_wav(question["text"], audio_path)
        except GeminiVoiceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return FileResponse(audio_path, media_type="audio/wav", filename=f"{question_id}.wav")


@app.post("/api/sessions/{session_id}/answer")
def submit_answer(session_id: str, payload: AnswerRequest, request: Request) -> dict[str, Any]:
    require_session_access(request, session_id, student_only=True)
    return submit_answer_for_session(session_id, payload, request)


@app.post("/api/student/attempts/current/answers")
def submit_current_answer(payload: AnswerRequest, request: Request) -> dict[str, Any]:
    auth = require_student_attempt(request)
    return submit_answer_for_session(auth["viva_session_id"], payload, request)


def submit_answer_for_session(session_id: str, payload: AnswerRequest, request: Request) -> dict[str, Any]:
    idempotency_key = request.headers.get("Idempotency-Key") or request.headers.get("X-Idempotency-Key")
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required.")

    question: dict[str, Any]
    exam: dict[str, Any]
    answer_id = str(uuid.uuid4())
    now = utc_now()
    with connect() as conn:
        session = row_to_dict(conn.execute("SELECT * FROM viva_sessions WHERE id = ?", (session_id,)).fetchone())
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if session["status"] != "active":
            raise HTTPException(status_code=409, detail="Session is not active")
        existing_by_key = row_to_dict(
            conn.execute("SELECT * FROM answers WHERE session_id = ? AND idempotency_key = ?", (session_id, idempotency_key)).fetchone()
        )
        if existing_by_key:
            return hydrate_session(conn, session_id)
        if session.get("current_question_id") and session["current_question_id"] != payload.question_id:
            raise HTTPException(status_code=409, detail="Answer does not match the server-controlled current question.")
        question = row_to_dict(conn.execute("SELECT * FROM questions WHERE id = ? AND session_id = ?", (payload.question_id, session_id)).fetchone())
        if not question:
            raise HTTPException(status_code=404, detail="Question not found")
        existing_for_question = row_to_dict(conn.execute("SELECT * FROM answers WHERE session_id = ? AND question_id = ?", (session_id, payload.question_id)).fetchone())
        if existing_for_question:
            raise HTTPException(status_code=409, detail="This question already has a submitted answer.")
        exam = row_to_dict(conn.execute("SELECT * FROM exams WHERE id = ?", (session["exam_id"],)).fetchone())
        question["expected_points"] = from_json(question["expected_points_json"], [])
        answer_text = canonical_answer_text(conn, session_id, payload)
        if not answer_text:
            raise HTTPException(status_code=400, detail="Answer cannot be empty.")
        try:
            conn.execute(
                """
                INSERT INTO answers (
                    id, session_id, question_id, input_mode, answer_text, score, max_score, reasoning,
                    audio_ref, created_at, scoring_status, scorer_provider, idempotency_key
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    answer_id,
                    session_id,
                    payload.question_id,
                    payload.input_mode,
                    answer_text,
                    0,
                    10,
                    "AI scoring pending.",
                    payload.audio_ref,
                    now,
                    "pending",
                    selected_ai_provider(),
                    idempotency_key,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Duplicate answer submission.") from exc
        log_transcript(conn, session_id, "answer_received", {"question_id": payload.question_id, "answer_id": answer_id, "input_mode": payload.input_mode})

    result, scorer_provider, scorer_error = score_current_answer(question, answer_text, exam)
    note_provider_outcome(scorer_provider, scorer_error)
    response_hash = hmac_hex(as_json(result))
    scoring_status = result.get("status", "scored")

    with connect() as conn:
        conn.execute(
            """
            UPDATE answers
            SET score = ?, max_score = ?, reasoning = ?, scoring_status = ?, scorer_provider = ?, scorer_error = ?,
                rubric_breakdown_json = ?, expected_points_covered_json = ?, expected_points_missed_json = ?,
                concerns_json = ?, response_hash = ?
            WHERE id = ?
            """,
            (
                result["score"],
                result["max_score"],
                result["reasoning"],
                scoring_status,
                scorer_provider,
                scorer_error,
                as_json(result.get("rubric_breakdown", {})),
                as_json(result.get("expected_points_covered", [])),
                as_json(result.get("expected_points_missed", [])),
                as_json(result.get("concerns", [])),
                response_hash,
                answer_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO scoring_runs (
                id, answer_id, status, provider, model, score, max_score, rubric_breakdown_json,
                expected_points_covered_json, expected_points_missed_json, concerns_json, reasoning,
                prompt_version, response_hash, error, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                answer_id,
                scoring_status,
                scorer_provider,
                result.get("model"),
                result["score"],
                result["max_score"],
                as_json(result.get("rubric_breakdown", {})),
                as_json(result.get("expected_points_covered", [])),
                as_json(result.get("expected_points_missed", [])),
                as_json(result.get("concerns", [])),
                result["reasoning"],
                result.get("prompt_version", "v1"),
                response_hash,
                scorer_error,
                utc_now(),
            ),
        )
        log_transcript(
            conn,
            session_id,
            "answer_scored",
            {
                "question_id": payload.question_id,
                "question_text": question["text"],
                "answer_id": answer_id,
                "input_mode": payload.input_mode,
                "score": result["score"],
                "max_score": result["max_score"],
                "reasoning": result["reasoning"],
                "scoring_status": scoring_status,
                "scorer_provider": scorer_provider,
                "scorer_error": scorer_error,
                "response_hash": response_hash,
            },
        )

        followup, followup_provider, followup_error = create_next_followup(question, answer_text, exam)
        note_provider_outcome(followup_provider, followup_error)
        if followup_provider or followup_error:
            log_transcript(
                conn,
                session_id,
                "followup_decision",
                {"provider": followup_provider, "error": followup_error, "created": bool(followup)},
            )
        if followup:
            next_ordinal = conn.execute("SELECT COALESCE(MAX(ordinal), 0) + 1 FROM questions WHERE session_id = ?", (session_id,)).fetchone()[0]
            followup_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO questions (id, session_id, ordinal, category, text, expected_points_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    followup_id,
                    session_id,
                    next_ordinal,
                    "follow-up",
                    followup,
                    as_json(["Concrete example", "Justification", "Technical specificity"]),
                    now,
                ),
            )
            conn.execute("UPDATE viva_sessions SET current_index = current_index + 1, current_question_id = ? WHERE id = ?", (followup_id, session_id))
        else:
            next_question = row_to_dict(
                conn.execute(
                    """
                    SELECT q.id
                    FROM questions q
                    LEFT JOIN answers a ON a.question_id = q.id
                    WHERE q.session_id = ? AND a.id IS NULL
                    ORDER BY q.ordinal
                    LIMIT 1
                    """,
                    (session_id,),
                ).fetchone()
            )
            conn.execute(
                "UPDATE viva_sessions SET current_index = current_index + 1, current_question_id = ? WHERE id = ?",
                (next_question["id"] if next_question else None, session_id),
            )

        # Do NOT auto-finalize when the last question is answered. The student must
        # explicitly submit the viva (POST /finalize) via the "Submit viva for review"
        # button, so the session stays `active` with current_question = null until then.
        # This keeps a clear, deliberate completion step (and lets them review before sending).
        return hydrate_session(conn, session_id)


@app.post("/api/sessions/{session_id}/audio")
async def upload_audio(session_id: str, request: Request, audio: UploadFile = File(...)) -> dict[str, Any]:
    require_session_access(request, session_id, student_only=True)
    draft_transcript = (await request.form()).get("draft_transcript")
    return await upload_audio_for_session(session_id, audio, str(draft_transcript or ""))


@app.post("/api/student/attempts/current/audio")
async def upload_current_audio(request: Request, audio: UploadFile = File(...)) -> dict[str, Any]:
    auth = require_student_attempt(request)
    form = await request.form()
    draft_transcript = str(form.get("draft_transcript") or "")
    audio_file = form.get("audio")
    if isinstance(audio_file, UploadFile):
        audio = audio_file
    return await upload_audio_for_session(auth["viva_session_id"], audio, draft_transcript)


@app.post("/api/sessions/{session_id}/recording")
async def upload_recording(session_id: str, request: Request, recording: UploadFile = File(...)) -> dict[str, Any]:
    require_session_access(request, session_id, student_only=True)
    return await store_session_recording(session_id, recording)


@app.post("/api/student/attempts/current/recording")
async def upload_current_recording(request: Request, recording: UploadFile = File(...)) -> dict[str, Any]:
    auth = require_student_attempt(request)
    form = await request.form()
    file = form.get("recording")
    if isinstance(file, UploadFile):
        recording = file
    return await store_session_recording(auth["viva_session_id"], recording)


async def store_session_recording(session_id: str, recording: UploadFile) -> dict[str, Any]:
    """Persist the full-viva webcam recording for examiner review (review-only, never scored)."""
    with connect() as conn:
        session = row_to_dict(conn.execute("SELECT * FROM viva_sessions WHERE id = ?", (session_id,)).fetchone())
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        # The full-viva recording is flushed at the END of the viva, so the session is
        # normally already `completed` by the time this fires. Accept both active (mid-viva
        # cap reached / early flush) and completed (the common case); reject anything else.
        if session["status"] not in ("active", "completed"):
            raise HTTPException(status_code=409, detail="Session is not active")
    content = await recording.read()
    max_bytes = int(os.getenv("TWELVE_MAX_VIDEO_BYTES", str(500 * 1024 * 1024)))
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail="Recording upload is too large.")
    if not content:
        raise HTTPException(status_code=400, detail="Recording is empty.")
    recording_dir = UPLOAD_DIR / "recordings" / session_id
    recording_dir.mkdir(parents=True, exist_ok=True)
    extension = Path(recording.filename or "viva.webm").suffix or ".webm"
    storage_path = recording_dir / f"{uuid.uuid4()}{extension}"
    storage_path.write_bytes(content)
    recording_id = str(uuid.uuid4())
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO session_recordings (id, session_id, storage_path, mime_type, size_bytes, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (recording_id, session_id, str(storage_path), recording.content_type, len(content), now),
        )
        log_transcript(
            conn,
            session_id,
            "recording_uploaded",
            {"recording_id": recording_id, "mime_type": recording.content_type, "size_bytes": len(content)},
        )
    return {"recording_id": recording_id, "size_bytes": len(content)}


async def upload_audio_for_session(session_id: str, audio: UploadFile, draft_transcript: str = "") -> dict[str, Any]:
    with connect() as conn:
        session = row_to_dict(conn.execute("SELECT * FROM viva_sessions WHERE id = ?", (session_id,)).fetchone())
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if session["status"] != "active":
            raise HTTPException(status_code=409, detail="Session is not active")
    content = await audio.read()
    max_bytes = int(os.getenv("TWELVE_MAX_AUDIO_BYTES", str(25 * 1024 * 1024)))
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail="Audio upload is too large.")
    audio_dir = UPLOAD_DIR / "audio" / session_id
    audio_dir.mkdir(parents=True, exist_ok=True)
    extension = Path(audio.filename or "answer.webm").suffix or ".webm"
    filename = f"{uuid.uuid4()}{extension}"
    storage_path = audio_dir / filename
    storage_path.write_bytes(content)
    audio_id = str(uuid.uuid4())
    provider = transcription_provider()
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO audio_submissions (
                id, session_id, question_id, storage_path, mime_type, size_bytes, draft_transcript,
                transcription_status, transcription_provider, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audio_id,
                session_id,
                session.get("current_question_id"),
                str(storage_path),
                audio.content_type,
                len(content),
                draft_transcript.strip() or None,
                "pending",
                provider,
                now,
            ),
        )
        log_transcript(
            conn,
            session_id,
            "audio_uploaded",
            {"audio_id": audio_id, "question_id": session.get("current_question_id"), "mime_type": audio.content_type, "size_bytes": len(content)},
        )

    try:
        transcript = transcribe_audio(storage_path, audio.content_type, draft_transcript)
    except TranscriptionError as exc:
        transcript = {
            "status": "pending_transcription_error",
            "text": "",
            "provider": provider,
            "model": None,
            "error": str(exc),
        }

    with connect() as conn:
        conn.execute(
            """
            UPDATE audio_submissions
            SET transcript_text = ?, transcription_status = ?, transcription_provider = ?,
                transcription_model = ?, transcription_error = ?, transcribed_at = ?
            WHERE id = ?
            """,
            (
                transcript["text"] or None,
                transcript["status"],
                transcript["provider"],
                transcript.get("model"),
                transcript.get("error"),
                utc_now(),
                audio_id,
            ),
        )
        log_transcript(
            conn,
            session_id,
            "audio_transcribed",
            {
                "audio_id": audio_id,
                "question_id": session.get("current_question_id"),
                "status": transcript["status"],
                "provider": transcript["provider"],
                "model": transcript.get("model"),
                "error": transcript.get("error"),
                "text_length": len(transcript["text"] or ""),
            },
        )
    return {
        "audio_ref": str(storage_path),
        "audio_id": audio_id,
        "transcription_status": transcript["status"],
        "transcription_provider": transcript["provider"],
        "transcription_model": transcript.get("model"),
        "transcript_text": transcript["text"],
        "transcription_error": transcript.get("error"),
    }


def canonical_answer_text(conn: Any, session_id: str, payload: AnswerRequest) -> str:
    if payload.input_mode == "typed":
        return payload.answer_text.strip()
    if not payload.audio_ref:
        raise HTTPException(status_code=400, detail="Voice answers require an uploaded audio reference.")
    audio = row_to_dict(
        conn.execute(
            "SELECT * FROM audio_submissions WHERE session_id = ? AND storage_path = ? ORDER BY created_at DESC LIMIT 1",
            (session_id, payload.audio_ref),
        ).fetchone()
    )
    if not audio:
        raise HTTPException(status_code=404, detail="Audio reference was not found for this attempt.")
    if audio["transcription_status"] in {"transcribed", "draft_used"} and audio.get("transcript_text"):
        return audio["transcript_text"].strip()
    # Self-supplied browser draft as the scored source is a non-prod convenience only.
    # DEFAULT-DENY: an unset/unknown TWELVE_ENV is treated as production, so a real deploy
    # that forgot to set the env can never accept a client-supplied transcript as truth.
    if self_supplied_transcript_allowed() and payload.answer_text.strip():
        conn.execute(
            """
            UPDATE audio_submissions
            SET transcript_text = ?, transcription_status = ?, transcription_provider = ?, transcription_model = ?, transcribed_at = ?
            WHERE id = ?
            """,
            (payload.answer_text.strip(), "draft_used", "browser-draft-local", "browser-speech-recognition", utc_now(), audio["id"]),
        )
        return payload.answer_text.strip()
    raise HTTPException(status_code=409, detail="Server transcription is not available yet. Retry after transcription or submit a typed answer.")


@app.post("/api/sessions/{session_id}/proctoring-events")
def log_proctoring_event(session_id: str, payload: ProctoringEventRequest, request: Request) -> dict[str, Any]:
    require_session_access(request, session_id, student_only=True)
    return log_proctoring_for_session(session_id, payload)


@app.post("/api/student/attempts/current/proctoring-events")
def log_current_proctoring_event(payload: ProctoringEventRequest, request: Request) -> dict[str, Any]:
    auth = require_student_attempt(request)
    return log_proctoring_for_session(auth["viva_session_id"], payload)


@app.post("/api/student/attempts/current/live-turn-events")
def log_current_live_turn_event(payload: LiveTurnEventRequest, request: Request) -> dict[str, Any]:
    auth = require_student_attempt(request)
    with connect() as conn:
        session = row_to_dict(conn.execute("SELECT current_question_id FROM viva_sessions WHERE id = ?", (auth["viva_session_id"],)).fetchone())
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        question_id = payload.question_id or session.get("current_question_id")
        if question_id and question_id != session.get("current_question_id"):
            raise HTTPException(status_code=409, detail="Live turn event does not match the current question.")
        event = {
            "id": str(uuid.uuid4()),
            "session_id": auth["viva_session_id"],
            "question_id": question_id,
            "event_type": payload.event_type,
            "payload": payload.payload,
            "created_at": utc_now(),
        }
        log_transcript(conn, auth["viva_session_id"], "live_turn_event", event)
        return event


def log_proctoring_for_session(session_id: str, payload: ProctoringEventRequest) -> dict[str, Any]:
    with connect() as conn:
        session = row_to_dict(conn.execute("SELECT * FROM viva_sessions WHERE id = ?", (session_id,)).fetchone())
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        event = {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "question_id": session.get("current_question_id"),
            "event_type": payload.event_type,
            "details": payload.details,
            "confidence": payload.confidence,
            "duration_ms": payload.duration_ms,
            "severity": payload.severity,
            "created_at": utc_now(),
        }
        conn.execute(
            """
            INSERT INTO proctoring_events (id, session_id, question_id, event_type, details_json, confidence, duration_ms, severity, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["id"],
                session_id,
                event["question_id"],
                payload.event_type,
                as_json(payload.details),
                payload.confidence,
                payload.duration_ms,
                payload.severity,
                event["created_at"],
            ),
        )
        log_transcript(conn, session_id, "proctoring_flag", event)
        return event


@app.post("/api/sessions/{session_id}/finalize")
def finalize(session_id: str, request: Request) -> dict[str, Any]:
    require_session_access(request, session_id, student_only=True)
    with connect() as conn:
        finalize_session(conn, session_id)
        return hydrate_session(conn, session_id)


@app.post("/api/review/sessions/{session_id}/override")
def create_score_review(session_id: str, payload: ReviewRequest, request: Request) -> dict[str, Any]:
    auth = require_staff(request, {"super_admin", "examiner"})
    if not payload.reason.strip():
        raise HTTPException(status_code=400, detail="Score override reason is required.")
    with connect() as conn:
        target = row_to_dict(conn.execute("SELECT exam_id FROM viva_sessions WHERE id = ?", (session_id,)).fetchone())
        if not target:
            raise HTTPException(status_code=404, detail="Session not found")
        require_exam_access(auth, target["exam_id"])
        review = {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "reviewer": payload.reviewer,
            "override_score": payload.override_score,
            "reason": payload.reason,
            "created_at": utc_now(),
        }
        conn.execute(
            "INSERT INTO score_reviews (id, session_id, reviewer, override_score, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (review["id"], session_id, review["reviewer"], review["override_score"], review["reason"], review["created_at"]),
        )
        audit_event(conn, "staff", get_auth_session(request, require_csrf=False)["user_id"], "score_override_created", {"session_id": session_id, "review_id": review["id"]})
        return review


def persist_scoring(conn: Any, answer_id: str, result: dict[str, Any], scorer_provider: str, scorer_error: str | None) -> tuple[str, str]:
    """Write a scoring result onto an answer and append an immutable scoring_runs record."""
    response_hash = hmac_hex(as_json(result))
    scoring_status = result.get("status", "scored")
    conn.execute(
        """
        UPDATE answers
        SET score = ?, max_score = ?, reasoning = ?, scoring_status = ?, scorer_provider = ?, scorer_error = ?,
            rubric_breakdown_json = ?, expected_points_covered_json = ?, expected_points_missed_json = ?,
            concerns_json = ?, response_hash = ?
        WHERE id = ?
        """,
        (
            result["score"],
            result["max_score"],
            result["reasoning"],
            scoring_status,
            scorer_provider,
            scorer_error,
            as_json(result.get("rubric_breakdown", {})),
            as_json(result.get("expected_points_covered", [])),
            as_json(result.get("expected_points_missed", [])),
            as_json(result.get("concerns", [])),
            response_hash,
            answer_id,
        ),
    )
    conn.execute(
        """
        INSERT INTO scoring_runs (
            id, answer_id, status, provider, model, score, max_score, rubric_breakdown_json,
            expected_points_covered_json, expected_points_missed_json, concerns_json, reasoning,
            prompt_version, response_hash, error, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            answer_id,
            scoring_status,
            scorer_provider,
            result.get("model"),
            result["score"],
            result["max_score"],
            as_json(result.get("rubric_breakdown", {})),
            as_json(result.get("expected_points_covered", [])),
            as_json(result.get("expected_points_missed", [])),
            as_json(result.get("concerns", [])),
            result["reasoning"],
            result.get("prompt_version", "v1"),
            response_hash,
            scorer_error,
            utc_now(),
        ),
    )
    return scoring_status, response_hash


def recompute_answer_score(session_id: str, answer_id: str, request: Request) -> None:
    """Re-run AI scoring for one answer against its CURRENT answer_text; re-finalize a completed session."""
    with connect() as conn:
        answer = row_to_dict(
            conn.execute("SELECT * FROM answers WHERE id = ? AND session_id = ?", (answer_id, session_id)).fetchone()
        )
        if not answer:
            raise HTTPException(status_code=404, detail="Answer not found for this session.")
        question = row_to_dict(conn.execute("SELECT * FROM questions WHERE id = ?", (answer["question_id"],)).fetchone())
        if not question:
            raise HTTPException(status_code=404, detail="Question not found for this answer.")
        question["expected_points"] = from_json(question["expected_points_json"], [])
        session = row_to_dict(conn.execute("SELECT * FROM viva_sessions WHERE id = ?", (session_id,)).fetchone())
        exam = row_to_dict(conn.execute("SELECT * FROM exams WHERE id = ?", (session["exam_id"],)).fetchone())

    result, scorer_provider, scorer_error = score_current_answer(question, answer["answer_text"], exam)
    note_provider_outcome(scorer_provider, scorer_error)

    with connect() as conn:
        scoring_status, _ = persist_scoring(conn, answer_id, result, scorer_provider, scorer_error)
        log_transcript(
            conn,
            session_id,
            "answer_rescored",
            {"answer_id": answer_id, "question_id": answer["question_id"], "score": result["score"], "scoring_status": scoring_status, "scorer_provider": scorer_provider, "scorer_error": scorer_error},
        )
        audit_event(conn, "staff", get_auth_session(request, require_csrf=False)["user_id"], "answer_rescored", {"session_id": session_id, "answer_id": answer_id, "status": scoring_status})
        # A completed session's final_score was computed with the stale (often zero) score; recompute it.
        # needs_review sessions (all answers had pending_ai_error) also re-finalize once a rescore succeeds.
        if session and session["status"] in {"completed", "needs_review"}:
            finalize_session(conn, session_id)


@app.post("/api/review/sessions/{session_id}/answers/{answer_id}/rescore")
def rescore_answer(session_id: str, answer_id: str, request: Request) -> dict[str, Any]:
    require_staff(request, {"super_admin", "examiner"})
    recompute_answer_score(session_id, answer_id, request)
    with connect() as conn:
        return hydrate_session(conn, session_id, include_private=True)


@app.post("/api/review/sessions/{session_id}/audio/{audio_id}/retranscribe")
def retranscribe_audio(session_id: str, audio_id: str, request: Request) -> dict[str, Any]:
    require_staff(request, {"super_admin", "examiner"})
    with connect() as conn:
        audio = row_to_dict(
            conn.execute("SELECT * FROM audio_submissions WHERE id = ? AND session_id = ?", (audio_id, session_id)).fetchone()
        )
        if not audio:
            raise HTTPException(status_code=404, detail="Audio submission not found for this session.")
    storage_path = Path(audio["storage_path"])
    if not storage_path.exists():
        raise HTTPException(status_code=404, detail="Stored audio file is missing.")
    try:
        transcript = transcribe_audio(storage_path, audio.get("mime_type"), audio.get("draft_transcript") or "")
    except TranscriptionError as exc:
        transcript = {"status": "pending_transcription_error", "text": "", "provider": transcription_provider(), "model": None, "error": str(exc)}

    with connect() as conn:
        conn.execute(
            """
            UPDATE audio_submissions
            SET transcript_text = ?, transcription_status = ?, transcription_provider = ?,
                transcription_model = ?, transcription_error = ?, transcribed_at = ?
            WHERE id = ?
            """,
            (
                transcript["text"] or None,
                transcript["status"],
                transcript["provider"],
                transcript.get("model"),
                transcript.get("error"),
                utc_now(),
                audio_id,
            ),
        )
        log_transcript(
            conn,
            session_id,
            "audio_retranscribed",
            {"audio_id": audio_id, "status": transcript["status"], "provider": transcript["provider"], "model": transcript.get("model"), "error": transcript.get("error"), "text_length": len(transcript["text"] or "")},
        )
        audit_event(conn, "staff", get_auth_session(request, require_csrf=False)["user_id"], "audio_retranscribed", {"session_id": session_id, "audio_id": audio_id, "status": transcript["status"]})

    # A recovered transcript is useless if the graded answer keeps its stale text:
    # sync it into the linked answer and re-score so the grade reflects the new words.
    rescored_answer_id: str | None = None
    if transcript["text"] and transcript["status"] in {"transcribed", "draft_used"}:
        with connect() as conn:
            linked = row_to_dict(
                conn.execute(
                    "SELECT id FROM answers WHERE session_id = ? AND audio_ref = ? ORDER BY created_at DESC LIMIT 1",
                    (session_id, audio["storage_path"]),
                ).fetchone()
            )
            if linked:
                conn.execute("UPDATE answers SET answer_text = ? WHERE id = ?", (transcript["text"].strip(), linked["id"]))
                log_transcript(conn, session_id, "answer_text_resynced", {"answer_id": linked["id"], "audio_id": audio_id, "text_length": len(transcript["text"].strip())})
                rescored_answer_id = linked["id"]
        if rescored_answer_id:
            recompute_answer_score(session_id, rescored_answer_id, request)
    return {
        "audio_id": audio_id,
        "transcription_status": transcript["status"],
        "transcription_provider": transcript["provider"],
        "transcription_model": transcript.get("model"),
        "transcript_text": transcript["text"],
        "transcription_error": transcript.get("error"),
        "rescored_answer_id": rescored_answer_id,
    }


REVIEW_ROLES = {"super_admin", "exam_admin", "examiner", "invigilator"}


@app.get("/api/review/exams")
def list_review_exams(request: Request, include_archived: bool = False) -> list[dict[str, Any]]:
    """Class-level overview: every exam the staffer can access, with how many of its
    enrolled students have taken / completed the viva. Archived exams are excluded by default.
    Examiners/invigilators see only exams assigned to them; admins see all."""
    auth = require_staff(request, REVIEW_ROLES)
    scope = accessible_exam_ids(auth)
    with connect() as conn:
        exams = rows_to_dicts(conn.execute("SELECT * FROM exams ORDER BY archived_at IS NOT NULL, created_at DESC"))
        result: list[dict[str, Any]] = []
        for exam in exams:
            if scope is not None and exam["id"] not in scope:
                continue
            if exam.get("archived_at") and not include_archived:
                continue
            student_count = conn.execute("SELECT COUNT(*) FROM students WHERE exam_id = ?", (exam["id"],)).fetchone()[0]
            taken = conn.execute("SELECT COUNT(DISTINCT student_id) FROM viva_sessions WHERE exam_id = ?", (exam["id"],)).fetchone()[0]
            completed = conn.execute(
                "SELECT COUNT(DISTINCT student_id) FROM viva_sessions WHERE exam_id = ? AND status = 'completed'", (exam["id"],)
            ).fetchone()[0]
            result.append(
                {
                    "id": exam["id"],
                    "name": exam["name"],
                    "status": exam.get("status"),
                    "mark_mode": exam.get("mark_mode"),
                    "created_at": exam.get("created_at"),
                    "archived": bool(exam.get("archived_at")),
                    "archived_at": exam.get("archived_at"),
                    "student_count": student_count,
                    "taken_count": taken,
                    "completed_count": completed,
                }
            )
        return result


@app.get("/api/review/exams/{exam_id}/class")
def review_exam_class(exam_id: str, request: Request) -> dict[str, Any]:
    """Whole-class roster for one exam: every enrolled student and their attempt status/score,
    including students who have not started yet."""
    auth = require_staff(request, REVIEW_ROLES)
    require_exam_access(auth, exam_id)
    with connect() as conn:
        exam = row_to_dict(conn.execute("SELECT * FROM exams WHERE id = ?", (exam_id,)).fetchone())
        if not exam:
            raise HTTPException(status_code=404, detail="Exam not found")
        students = rows_to_dicts(conn.execute("SELECT id, roll_number, name, email FROM students WHERE exam_id = ? ORDER BY roll_number", (exam_id,)))
        roster: list[dict[str, Any]] = []
        for student in students:
            session = row_to_dict(
                conn.execute(
                    "SELECT * FROM viva_sessions WHERE exam_id = ? AND student_id = ? ORDER BY started_at DESC LIMIT 1",
                    (exam_id, student["id"]),
                ).fetchone()
            )
            entry = {
                "student_id": student["id"],
                "roll_number": student["roll_number"],
                "name": student["name"],
                "email": student.get("email"),
                "attempt_status": "not_started",
                "session_id": None,
                "final_score": None,
                "effective_score": None,
                "started_at": None,
                "ended_at": None,
            }
            if session:
                meta = attach_session_list_metadata(conn, [dict(session)])[0]
                entry.update(
                    attempt_status=session["status"],
                    session_id=session["id"],
                    final_score=session.get("final_score"),
                    effective_score=meta.get("effective_score"),
                    started_at=session.get("started_at"),
                    ended_at=session.get("ended_at"),
                )
            roster.append(entry)
        return {
            "exam": {"id": exam["id"], "name": exam["name"], "mark_mode": exam.get("mark_mode"), "archived": bool(exam.get("archived_at"))},
            "student_count": len(students),
            "taken_count": sum(1 for r in roster if r["attempt_status"] != "not_started"),
            "completed_count": sum(1 for r in roster if r["attempt_status"] == "completed"),
            "roster": roster,
        }


@app.get("/api/review/sessions")
def list_review_sessions(request: Request) -> list[dict[str, Any]]:
    auth = require_staff(request, REVIEW_ROLES)
    scope = accessible_exam_ids(auth)
    with connect() as conn:
        sessions = rows_to_dicts(
            conn.execute(
                """
                SELECT vs.*, e.name AS exam_name, e.mark_mode, s.name AS student_name, s.roll_number
                FROM viva_sessions vs
                JOIN exams e ON e.id = vs.exam_id
                JOIN students s ON s.id = vs.student_id
                ORDER BY vs.started_at DESC
                """
            )
        )
        if scope is not None:
            sessions = [s for s in sessions if s["exam_id"] in scope]
        return attach_session_list_metadata(conn, sessions)


@app.get("/api/review/sessions/{session_id}")
def review_session(session_id: str, request: Request) -> dict[str, Any]:
    auth = require_staff(request, REVIEW_ROLES)
    with connect() as conn:
        session = row_to_dict(conn.execute("SELECT exam_id FROM viva_sessions WHERE id = ?", (session_id,)).fetchone())
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        require_exam_access(auth, session["exam_id"])
        return hydrate_session(conn, session_id, include_private=True)


@app.get("/api/review/exams/{exam_id}/sessions")
def list_review_exam_sessions(exam_id: str, request: Request) -> list[dict[str, Any]]:
    auth = require_staff(request, REVIEW_ROLES)
    require_exam_access(auth, exam_id)
    with connect() as conn:
        sessions = rows_to_dicts(
            conn.execute(
                """
                SELECT vs.*, e.name AS exam_name, e.mark_mode, s.name AS student_name, s.roll_number
                FROM viva_sessions vs
                JOIN exams e ON e.id = vs.exam_id
                JOIN students s ON s.id = vs.student_id
                WHERE vs.exam_id = ?
                ORDER BY vs.started_at DESC
                """,
                (exam_id,),
            )
        )
        return attach_session_list_metadata(conn, sessions)


@app.post("/api/review/sessions/{session_id}/score-review")
def create_score_review_alias(session_id: str, payload: ReviewRequest, request: Request) -> dict[str, Any]:
    return create_score_review(session_id, payload, request)


@app.get("/api/review/audio")
def review_audio(ref: str, request: Request) -> FileResponse:
    require_staff(request, {"super_admin", "examiner", "invigilator"})
    path = Path(ref).resolve()
    upload_root = UPLOAD_DIR.resolve()
    if upload_root not in path.parents:
        raise HTTPException(status_code=403, detail="Audio reference is not available.")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Audio not found.")
    return FileResponse(path, media_type="audio/webm", filename=path.name)


@app.get("/api/review/recording")
def review_recording(ref: str, request: Request) -> FileResponse:
    require_staff(request, {"super_admin", "examiner", "invigilator"})
    path = Path(ref).resolve()
    upload_root = UPLOAD_DIR.resolve()
    if upload_root not in path.parents:
        raise HTTPException(status_code=403, detail="Recording reference is not available.")
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Recording not found.")
    return FileResponse(path, media_type="video/webm", filename=path.name)


@app.post("/api/realtime/token")
async def realtime_token(request: Request) -> dict[str, Any]:
    require_provider_token_access(request)
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime-mini")
    if not api_key:
        return {
            "configured": False,
            "message": "OPENAI_API_KEY is not configured. The browser will use local speech synthesis and typed/recorded answers.",
        }

    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            "https://api.openai.com/v1/realtime/sessions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "voice": "alloy",
                "instructions": "You are TWELVE, an AI viva examiner. Ask concise academic questions and wait for the student's answer.",
            },
        )
    if response.status_code >= 400:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    data = response.json()
    data["configured"] = True
    return data


@app.post("/api/gemini/live-token")
def gemini_live_token(request: Request) -> dict[str, Any]:
    require_provider_token_access(request)
    if not gemini_configured():
        return {
            "configured": False,
            "message": "GEMINI_API_KEY is not configured. Gemini Live cannot mint an ephemeral token.",
        }
    try:
        return create_live_ephemeral_token()
    except GeminiVoiceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/student/attempts/current/live-token")
def current_live_token(request: Request) -> dict[str, Any]:
    require_student_attempt(request)
    return gemini_live_token(request)


def ensure_env_bootstrap_admin() -> None:
    email = os.getenv("TWELVE_BOOTSTRAP_ADMIN_EMAIL")
    password = os.getenv("TWELVE_BOOTSTRAP_ADMIN_PASSWORD")
    if not email or not password:
        return
    with connect() as conn:
        existing = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if existing:
            return
        user_id = create_staff_user(conn, email, os.getenv("TWELVE_BOOTSTRAP_ADMIN_NAME", "TWELVE Admin"), password, ["super_admin", "exam_admin", "examiner", "invigilator"])
        audit_event(conn, "staff", user_id, "staff_bootstrapped_from_env", {"email": email})


def normalize_legacy_data() -> None:
    with connect() as conn:
        for student in rows_to_dicts(conn.execute("SELECT id, token, token_hash FROM students")):
            if not student.get("token_hash") and student.get("token") and not str(student["token"]).startswith("invite:"):
                conn.execute("UPDATE students SET token_hash = ?, token = ? WHERE id = ?", (hash_opaque_token(student["token"]), f"invite:{uuid.uuid4()}", student["id"]))
        sessions = rows_to_dicts(conn.execute("SELECT id FROM viva_sessions WHERE current_question_id IS NULL AND status = 'active'"))
        for session in sessions:
            question = row_to_dict(
                conn.execute(
                    """
                    SELECT q.id
                    FROM questions q
                    LEFT JOIN answers a ON a.question_id = q.id
                    WHERE q.session_id = ? AND a.id IS NULL
                    ORDER BY q.ordinal
                    LIMIT 1
                    """,
                    (session["id"],),
                ).fetchone()
            )
            if question:
                conn.execute("UPDATE viva_sessions SET current_question_id = ? WHERE id = ?", (question["id"], session["id"]))


def create_staff_user(conn: Any, email: str, name: str, password: str, roles: list[str]) -> str:
    if len(password) < 10:
        raise HTTPException(status_code=400, detail="Password must be at least 10 characters.")
    user_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users (id, email, name, password_hash, active, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, email.strip().lower(), name.strip(), hash_password(password), 1, utc_now()),
    )
    for role in roles:
        conn.execute("INSERT INTO user_roles (user_id, role) VALUES (?, ?)", (user_id, role))
    return user_id


def issue_staff_session(conn: Any, response: Response, user_id: str) -> dict[str, Any]:
    token = generate_token(32)
    csrf_token = generate_token(24)
    conn.execute(
        """
        INSERT INTO auth_sessions (id, token_hash, csrf_token, user_id, role, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (str(uuid.uuid4()), hash_opaque_token(token), csrf_token, user_id, "staff", expires_at(STAFF_SESSION_HOURS), utc_now()),
    )
    set_session_cookies(response, token, csrf_token)
    roles = [row["role"] for row in conn.execute("SELECT role FROM user_roles WHERE user_id = ?", (user_id,))]
    user = row_to_dict(conn.execute("SELECT id, email, name FROM users WHERE id = ?", (user_id,)).fetchone())
    return {"role": "staff", "user": user, "roles": roles, "csrf_token": csrf_token}


def issue_student_session(conn: Any, response: Response, student_id: str, viva_session_id: str) -> dict[str, Any]:
    token = generate_token(32)
    csrf_token = generate_token(24)
    conn.execute(
        """
        INSERT INTO auth_sessions (id, token_hash, csrf_token, student_id, viva_session_id, role, expires_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (str(uuid.uuid4()), hash_opaque_token(token), csrf_token, student_id, viva_session_id, "student", expires_at(STUDENT_SESSION_HOURS), utc_now()),
    )
    set_session_cookies(response, token, csrf_token)
    return {"role": "student", "student_id": student_id, "viva_session_id": viva_session_id, "csrf_token": csrf_token}


def set_session_cookies(response: Response, token: str, csrf_token: str) -> None:
    options = session_cookie_options()
    response.set_cookie(AUTH_COOKIE, token, **options)
    response.set_cookie(CSRF_COOKIE, csrf_token, httponly=False, secure=options["secure"], samesite="lax", path="/")


def get_auth_session(request: Request, require_csrf: bool = True) -> dict[str, Any]:
    token = request.cookies.get(AUTH_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    with connect() as conn:
        session = row_to_dict(conn.execute("SELECT * FROM auth_sessions WHERE token_hash = ?", (hash_opaque_token(token),)).fetchone())
    if not session or is_expired(session["expires_at"]):
        raise HTTPException(status_code=401, detail="Session expired or invalid.")
    if require_csrf and request.method not in SAFE_METHODS:
        supplied = request.headers.get("X-CSRF-Token") or request.headers.get("x-csrf-token")
        if not supplied or not secrets.compare_digest(supplied, session["csrf_token"]):
            raise HTTPException(status_code=403, detail="CSRF token is missing or invalid.")
    return session


def require_staff(request: Request, roles: set[str]) -> dict[str, Any]:
    auth = get_auth_session(request)
    if auth["role"] != "staff" or not auth.get("user_id"):
        raise HTTPException(status_code=403, detail="Staff access required.")
    with connect() as conn:
        user_roles = {row["role"] for row in conn.execute("SELECT role FROM user_roles WHERE user_id = ?", (auth["user_id"],))}
    if roles and user_roles.isdisjoint(roles):
        raise HTTPException(status_code=403, detail="Insufficient role.")
    auth["roles"] = sorted(user_roles)
    return auth


def require_student_attempt(request: Request) -> dict[str, Any]:
    auth = get_auth_session(request)
    if auth["role"] != "student" or not auth.get("viva_session_id"):
        raise HTTPException(status_code=403, detail="Active student attempt required.")
    with connect() as conn:
        session = row_to_dict(conn.execute("SELECT * FROM viva_sessions WHERE id = ?", (auth["viva_session_id"],)).fetchone())
    if not session or session["status"] not in {"active", "completed"}:
        raise HTTPException(status_code=403, detail="Attempt is not available.")
    return auth


def require_session_access(request: Request, session_id: str, require_csrf: bool = True, student_only: bool = False) -> dict[str, Any]:
    """Authorize access to a viva session via the legacy /api/sessions/{id} routes.

    Students may only touch their own attempt. Staff get READ access to any session
    (the /review flows rely on this). student_only=True marks a write path that
    belongs to the student attempt lifecycle (answering, audio, proctoring, finalize):
    staff must NOT drive a student's session through those, so they are rejected even
    though staff can still read it.
    """
    auth = get_auth_session(request, require_csrf=require_csrf)
    if auth["role"] == "student":
        if auth.get("viva_session_id") != session_id:
            raise HTTPException(status_code=403, detail="Attempt ownership required.")
        return auth
    if auth["role"] == "staff":
        if student_only:
            raise HTTPException(status_code=403, detail="Staff cannot perform student attempt actions on a session.")
        return require_staff(request, {"super_admin", "exam_admin", "examiner", "invigilator"})
    raise HTTPException(status_code=403, detail="Access denied.")


def require_provider_token_access(request: Request) -> dict[str, Any]:
    auth = get_auth_session(request)
    actor_id = auth.get("viva_session_id") or auth.get("user_id")
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with connect() as conn:
        recent = conn.execute(
            """
            SELECT COUNT(*) FROM security_audit_events
            WHERE actor_id = ? AND event_type = 'provider_token_minted' AND created_at >= ?
            """,
            (actor_id, cutoff),
        ).fetchone()[0]
        if recent >= int(os.getenv("TWELVE_PROVIDER_TOKEN_RATE_PER_MINUTE", "4")):
            raise HTTPException(status_code=429, detail="Provider token rate limit exceeded.")
        if auth["role"] == "student":
            session = row_to_dict(conn.execute("SELECT status FROM viva_sessions WHERE id = ?", (auth["viva_session_id"],)).fetchone())
            if not session or session["status"] != "active":
                raise HTTPException(status_code=403, detail="Active attempt required.")
        elif auth["role"] == "staff":
            roles = {row["role"] for row in conn.execute("SELECT role FROM user_roles WHERE user_id = ?", (auth["user_id"],))}
            if roles.isdisjoint({"super_admin", "exam_admin", "examiner"}):
                raise HTTPException(status_code=403, detail="Staff test access denied.")
        audit_event(conn, auth["role"], actor_id, "provider_token_minted", {"path": str(request.url.path)})
    return auth


def audit_event(conn: Any, actor_type: str, actor_id: str | None, event_type: str, details: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO security_audit_events (id, actor_type, actor_id, event_type, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), actor_type, actor_id, event_type, as_json(details), utc_now()),
    )


def csv_row_count(csv_bytes: bytes) -> int:
    """Best-effort count of non-empty CSV data rows (excludes the header).

    parse_students_csv() is owned by another module and may silently drop rows
    (e.g. missing roll_number). Comparing this raw count against the parsed count
    lets create_exam surface a skipped-row warning without editing that module.
    """
    try:
        import csv as _csv
        import io as _io

        text = csv_bytes.decode("utf-8-sig", errors="replace")
        reader = _csv.reader(_io.StringIO(text))
        rows = [row for row in reader if any((cell or "").strip() for cell in row)]
        return max(len(rows) - 1, 0)  # subtract the header row
    except Exception:
        return 0


def infer_student_id(filename: str, student_ids: dict[str, str]) -> str | None:
    lowered = filename.lower()
    for roll, student_id in student_ids.items():
        if roll and roll in lowered:
            return student_id
    return None


# --- AI provider health (degraded-mode signalling for the student UI) ---------
# When the configured provider (e.g. Gemini) fails and we fall back to the local
# scorer, we flip a process-wide "degraded" flag. The student UI polls
# GET /api/ai/health on a heartbeat to show a banner, and that endpoint actively
# re-probes the provider (throttled) so the flag clears the moment it recovers.
AI_PROBE_INTERVAL_SECONDS = 12.0
_ai_health: dict[str, Any] = {
    "degraded": False,
    "since": None,        # ISO timestamp degradation began
    "last_error": None,
    "_last_probe": 0.0,    # time.monotonic() of the last recovery probe
}


def record_ai_outcome(healthy: bool, error: str | None = None) -> None:
    """Track whether the configured AI provider is serving (healthy) or we fell back."""
    if healthy:
        if _ai_health["degraded"]:
            _ai_health.update(degraded=False, since=None, last_error=None)
        return
    if not _ai_health["degraded"]:
        _ai_health.update(degraded=True, since=utc_now())
    _ai_health["last_error"] = error


def note_provider_outcome(provider_tag: str, error: str | None = None) -> None:
    """Update AI health from a dispatcher's returned provider tag.

    `local-fallback` means the configured AI provider failed and we degraded to the
    local scorer; a real provider tag (gemini/openai/ollama) means it served. A plain
    `local` tag (provider intentionally local) is ignored — there is nothing to degrade.
    """
    if provider_tag == "local-fallback":
        record_ai_outcome(False, error)
    elif provider_tag in ("gemini", "openai", "ollama"):
        record_ai_outcome(True)


def probe_active_provider() -> bool:
    """Cheaply check whether the configured provider is reachable again."""
    provider = selected_ai_provider()
    try:
        if provider == "gemini":
            gemini_health_check()
        elif provider == "ollama":
            ollama_health_check()
        else:
            # No cheap probe for openai/local; assume recovery is detected on next score.
            return not _ai_health["degraded"]
        return True
    except (GeminiAgentError, OllamaAgentError):
        return False


@app.get("/api/ai/health")
def ai_health() -> dict[str, Any]:
    """Operational status of the AI examiner, polled by the student heartbeat.

    Reports whether scoring is degraded to the local fallback. When degraded, it
    actively re-probes the configured provider (throttled to AI_PROBE_INTERVAL_SECONDS)
    so the flag clears the instant the provider recovers — flipping the UI back to
    full-AI mode without needing another answer to be scored.
    """
    provider = selected_ai_provider()
    if _ai_health["degraded"]:
        now = time.monotonic()
        if now - _ai_health["_last_probe"] >= AI_PROBE_INTERVAL_SECONDS:
            _ai_health["_last_probe"] = now
            if probe_active_provider():
                record_ai_outcome(True)
    degraded = bool(_ai_health["degraded"])
    return {
        "provider": provider,
        "degraded": degraded,
        "mode": "local-fallback" if degraded else "full",
        "since": _ai_health["since"],
    }


# Provider-specific error types; any of these triggers failover to the next provider.
AI_AGENT_ERRORS = (OpenAIAgentError, GeminiAgentError, OllamaAgentError)


def provider_attempt_order() -> list[str]:
    """Ordered list of real AI providers to try before giving up to the local scorer.

    The configured provider comes first, then the OTHER available providers as automatic
    failovers — so a transient error (e.g. Gemini 429 / quota) hands off to a working AI
    (typically the always-local Ollama) instead of silently dropping to the deterministic
    keyword scorer. An explicit `local` selection opts out of all AI (returns []).
    """
    selected = selected_ai_provider()
    if selected == "local":
        return []
    order: list[str] = []
    for candidate in [selected, "ollama", "gemini", "openai"]:
        if candidate in order or candidate == "local":
            continue
        if candidate == "gemini" and not gemini_configured():
            continue
        if candidate == "openai" and not openai_configured():
            continue
        if candidate == "ollama" and not ollama_configured():
            continue
        order.append(candidate)
    return order


def _local_tag() -> str:
    """`local` when local is the deliberate choice, else `local-fallback` (AI failed)."""
    return "local" if selected_ai_provider() == "local" else "local-fallback"


def make_question_plan(exam: dict[str, Any], student: dict[str, Any], submission_text: str) -> tuple[Any, str, str | None]:
    errors: list[str] = []
    for provider in provider_attempt_order():
        try:
            if provider == "openai":
                plan = build_question_plan_with_openai(exam, student, submission_text)
            elif provider == "gemini":
                plan = build_question_plan_with_gemini(exam, student, submission_text)
            else:
                plan = build_question_plan_with_ollama(exam, student, submission_text)
            return plan, provider, ("; ".join(errors) or None)
        except AI_AGENT_ERRORS as exc:
            errors.append(f"{provider}: {exc}")
    return build_question_plan(exam, student, submission_text), _local_tag() if errors else "local", ("; ".join(errors) or None)


def score_current_answer(question: dict[str, Any], answer_text: str, exam: dict[str, Any]) -> tuple[dict[str, Any], str, str | None]:
    errors: list[str] = []
    for provider in provider_attempt_order():
        try:
            if provider == "openai":
                result = score_answer_with_openai(question, answer_text, exam["rubric"], exam)
                result["model"] = os.getenv("OPENAI_VIVA_MODEL", "gpt-5.5")
            elif provider == "gemini":
                result = score_answer_with_gemini(question, answer_text, exam["rubric"], exam)
                result["model"] = os.getenv("GEMINI_VIVA_MODEL", "gemini-2.5-flash")
            else:
                result = score_answer_with_ollama(question, answer_text, exam["rubric"], exam)
                result["model"] = ollama_model()
            result["status"] = "scored"
            return result, provider, ("; ".join(errors) or None)
        except AI_AGENT_ERRORS as exc:
            errors.append(f"{provider}: {exc}")

    error_text = "; ".join(errors) or None
    # All AI providers failed (or local was chosen). In local/dev/test fall back to the
    # deterministic scorer; in staging/prod refuse to fabricate a mark.
    if selected_ai_provider() == "local" or local_ai_allowed():
        result = score_answer(question, answer_text, exam["rubric"])
        result["status"] = "scored"
        result["model"] = "local"
        return result, _local_tag() if errors else "local", error_text
    return pending_ai_error_result(error_text or "AI scoring failed"), selected_ai_provider(), error_text


def pending_ai_error_result(error: str) -> dict[str, Any]:
    return {
        "status": "pending_ai_error",
        "score": 0,
        "max_score": 10,
        "rubric_breakdown": {},
        "expected_points_covered": [],
        "expected_points_missed": [],
        "concerns": ["AI scoring failed and requires professor review or retry."],
        "reasoning": f"AI scoring failed: {error}",
        "model": None,
        "prompt_version": "v1",
    }


def local_ai_allowed() -> bool:
    return os.getenv("TWELVE_ENV", "local").strip().lower() in {"local", "development", "test"}


def self_supplied_transcript_allowed() -> bool:
    """Whether a client-supplied browser draft may stand in as the scored answer source.

    Unlike local_ai_allowed(), this does NOT default to a permissive env: an unset or
    unknown TWELVE_ENV is treated as production (deny). Only an explicit local/dev/test
    env opts in. This keeps a misconfigured real deploy from trusting client transcripts.
    """
    return os.getenv("TWELVE_ENV", "").strip().lower() in {"local", "development", "test"}


def create_next_followup(question: dict[str, Any], answer_text: str, exam: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    if question["category"] == "follow-up":
        return None, None, None
    errors: list[str] = []
    for provider in provider_attempt_order():
        try:
            if provider == "openai":
                return create_followup_with_openai(question, answer_text, exam["rubric"]), "openai", ("; ".join(errors) or None)
            if provider == "gemini":
                return create_followup_with_gemini(question, answer_text, exam["rubric"]), "gemini", ("; ".join(errors) or None)
            return create_followup_with_ollama(question, answer_text, exam["rubric"]), "ollama", ("; ".join(errors) or None)
        except AI_AGENT_ERRORS as exc:
            errors.append(f"{provider}: {exc}")
    return create_followup(question, answer_text), _local_tag() if errors else "local", ("; ".join(errors) or None)


def selected_ai_provider() -> str:
    provider = os.getenv("TWELVE_AI_PROVIDER", "auto").strip().lower()
    if provider == "local":
        return "local"
    if provider == "openai":
        return "openai" if openai_configured() else "local"
    if provider == "gemini":
        return "gemini" if gemini_configured() else "local"
    if provider == "ollama":
        return "ollama" if ollama_configured() else "local"
    if openai_configured():
        return "openai"
    if gemini_configured():
        return "gemini"
    return "local"


def hmac_hex(data: str) -> str:
    """Keyed (HMAC-SHA256) tamper-evidence digest.

    Used for answer response_hash and the transcript hash chain. Keying with the
    server secret stops a DB-level attacker from recomputing valid hashes after
    editing rows; a plain sha256 would be trivially forgeable.
    """
    return hmac.new(secret_key(), data.encode("utf-8"), hashlib.sha256).hexdigest()


def log_transcript(conn: Any, session_id: str, event_type: str, payload: dict[str, Any]) -> None:
    previous = row_to_dict(
        conn.execute(
            "SELECT sequence, event_hash FROM transcript_events WHERE session_id = ? ORDER BY COALESCE(sequence, 0) DESC, created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    )
    sequence = int(previous["sequence"] or 0) + 1 if previous else 1
    prev_hash = previous["event_hash"] if previous else ""
    created_at = utc_now()
    payload_json = as_json(payload)
    event_hash = hmac_hex(f"{session_id}|{sequence}|{prev_hash}|{event_type}|{payload_json}|{created_at}")
    conn.execute(
        """
        INSERT INTO transcript_events (id, session_id, type, payload_json, created_at, sequence, prev_hash, event_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (str(uuid.uuid4()), session_id, event_type, payload_json, created_at, sequence, prev_hash, event_hash),
    )


def finalize_session(conn: Any, session_id: str) -> None:
    answers = rows_to_dicts(conn.execute("SELECT score, max_score, scoring_status FROM answers WHERE session_id = ?", (session_id,)))
    # Answers whose AI scoring failed in staging/prod carry scoring_status=pending_ai_error
    # and a placeholder score of 0. Including them would fabricate a misleadingly low final
    # shown to the student. Exclude them from the mean (final = answer-scores only), and if
    # EVERY answer is unscored, route the session to needs_review instead of emitting a fake 0.
    scored = [a for a in answers if a.get("scoring_status") != "pending_ai_error"]
    pending = [a for a in answers if a.get("scoring_status") == "pending_ai_error"]
    if answers and not scored:
        # Nothing could be scored — do not show a 0; flag for professor review.
        conn.execute(
            "UPDATE viva_sessions SET status = ?, final_score = ?, ended_at = COALESCE(ended_at, ?) WHERE id = ?",
            ("needs_review", None, utc_now(), session_id),
        )
        conn.execute("UPDATE students SET active_session_id = NULL WHERE active_session_id = ?", (session_id,))
        log_transcript(conn, session_id, "session_needs_review", {"reason": "all_answers_pending_ai_error", "pending_count": len(pending)})
        return
    if not scored:
        final_score = 0.0
    else:
        earned = sum(answer["score"] for answer in scored)
        possible = sum(answer["max_score"] for answer in scored)
        final_score = round((earned / possible) * 100, 1) if possible else 0.0
    conn.execute(
        "UPDATE viva_sessions SET status = ?, final_score = ?, ended_at = COALESCE(ended_at, ?) WHERE id = ?",
        ("completed", final_score, utc_now(), session_id),
    )
    # active_session_id points at the student's in-progress attempt; clear it once finalized.
    conn.execute("UPDATE students SET active_session_id = NULL WHERE active_session_id = ?", (session_id,))
    log_transcript(conn, session_id, "session_finalized", {"final_score": final_score})


def hydrate_session(conn: Any, session_id: str, include_private: bool = False) -> dict[str, Any]:
    session = row_to_dict(
        conn.execute(
            """
            SELECT vs.*, e.name AS exam_name, e.rubric, e.mark_mode, s.name AS student_name, s.roll_number
            FROM viva_sessions vs
            JOIN exams e ON e.id = vs.exam_id
            JOIN students s ON s.id = vs.student_id
            WHERE vs.id = ?
            """,
            (session_id,),
        ).fetchone()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    questions = rows_to_dicts(conn.execute("SELECT * FROM questions WHERE session_id = ? ORDER BY ordinal", (session_id,)))
    for question in questions:
        question["expected_points"] = from_json(question.pop("expected_points_json"), [])

    answers = rows_to_dicts(conn.execute("SELECT * FROM answers WHERE session_id = ? ORDER BY created_at", (session_id,)))
    for answer in answers:
        answer["rubric_breakdown"] = from_json(answer.pop("rubric_breakdown_json", None), {})
        answer["expected_points_covered"] = from_json(answer.pop("expected_points_covered_json", None), [])
        answer["expected_points_missed"] = from_json(answer.pop("expected_points_missed_json", None), [])
        answer["concerns"] = from_json(answer.pop("concerns_json", None), [])
        if not include_private:
            answer.pop("response_hash", None)
            answer.pop("scorer_error", None)
    proctoring = rows_to_dicts(conn.execute("SELECT * FROM proctoring_events WHERE session_id = ? ORDER BY created_at", (session_id,)))
    for event in proctoring:
        event["details"] = from_json(event.pop("details_json"), {})

    transcript = rows_to_dicts(conn.execute("SELECT * FROM transcript_events WHERE session_id = ? ORDER BY created_at", (session_id,)))
    for event in transcript:
        event["payload"] = from_json(event.pop("payload_json"), {})

    audio_submissions = rows_to_dicts(
        conn.execute(
            """
            SELECT id, question_id, storage_path AS audio_ref, mime_type, size_bytes, draft_transcript,
                   transcript_text, transcription_status, transcription_provider, transcription_model,
                   transcription_error, created_at, transcribed_at
            FROM audio_submissions
            WHERE session_id = ?
            ORDER BY created_at
            """,
            (session_id,),
        )
    )
    if not include_private:
        for audio in audio_submissions:
            audio.pop("transcription_error", None)

    current_question = next((question for question in questions if question["id"] == session.get("current_question_id")), None)

    session["permissions"] = from_json(session.pop("permissions_json"), {})
    session["plan"] = from_json(session.pop("plan_json"), [])
    session["questions"] = questions
    session["answers"] = answers
    session["current_question"] = current_question
    session["proctoring_events"] = proctoring
    session["transcript_events"] = transcript
    session["audio_submissions"] = audio_submissions
    # Webcam recordings are staff-review-only; never expose the refs to the student.
    session["recordings"] = (
        rows_to_dicts(
            conn.execute(
                """
                SELECT id, storage_path AS ref, mime_type, size_bytes, created_at
                FROM session_recordings WHERE session_id = ? ORDER BY created_at
                """,
                (session_id,),
            )
        )
        if include_private
        else []
    )
    session["rubric"] = session["rubric"] if include_private else None
    # Deterministic tie-break (created_at, rowid) so reviews[-1] is the same newest
    # override that latest_override resolves to for the list endpoints.
    reviews = rows_to_dicts(conn.execute("SELECT * FROM score_reviews WHERE session_id = ? ORDER BY created_at, rowid", (session_id,)))
    # Students see their effective (possibly overridden) grade, but never the reviewer
    # identity or the private override reason — those stay staff-only.
    session["score_reviews"] = reviews if include_private else []
    apply_effective_score(session, reviews, include_private=include_private)
    return session


def latest_override(conn: Any, session_id: str) -> dict[str, Any] | None:
    return row_to_dict(
        conn.execute(
            "SELECT * FROM score_reviews WHERE session_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    )


def attach_session_list_metadata(conn: Any, sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach proctoring_count + effective-score fields to a session list in 2 queries (no N+1)."""
    if not sessions:
        return sessions
    ids = [session["id"] for session in sessions]
    placeholders = ",".join("?" for _ in ids)
    counts = {
        row["session_id"]: row["n"]
        for row in rows_to_dicts(
            conn.execute(
                f"SELECT session_id, COUNT(*) AS n FROM proctoring_events WHERE session_id IN ({placeholders}) GROUP BY session_id",
                ids,
            )
        )
    }
    # Ordered ascending with rowid tie-break, so the last row seen per session is the newest override.
    latest_by_session: dict[str, dict[str, Any]] = {}
    for review in rows_to_dicts(
        conn.execute(f"SELECT * FROM score_reviews WHERE session_id IN ({placeholders}) ORDER BY created_at, rowid", ids)
    ):
        latest_by_session[review["session_id"]] = review
    for session in sessions:
        session["proctoring_count"] = counts.get(session["id"], 0)
        override = latest_by_session.get(session["id"])
        apply_effective_score(session, [override] if override else [])
    return sessions


def apply_effective_score(session: dict[str, Any], reviews: list[dict[str, Any]] | None, include_private: bool = True) -> None:
    """Expose the grade a professor override implies without mutating the AI final_score audit value.

    reviewer identity and override reason are staff-only; with include_private=False
    (student-facing reads) the effective score is still surfaced but those fields are nulled.
    """
    latest = reviews[-1] if reviews else None
    if latest is not None:
        session["effective_score"] = latest["override_score"]
        session["score_overridden"] = True
        session["score_source"] = "professor_override"
        session["override_reviewer"] = latest.get("reviewer") if include_private else None
        session["override_reason"] = latest.get("reason") if include_private else None
    else:
        session["effective_score"] = session.get("final_score")
        session["score_overridden"] = False
        session["score_source"] = "ai"
        session["override_reviewer"] = None
        session["override_reason"] = None
    apply_mark_mode_status(session)


def apply_mark_mode_status(session: dict[str, Any]) -> None:
    """Resolve whether the effective score is official, per the exam's mark_mode.

    ai_official: the AI score is official as soon as the session completes (a professor
        override still supersedes it). professor_approved: the score stays provisional
        until a professor records an override/approval.
    """
    mark_mode = session.get("mark_mode") or "professor_approved"
    completed = session.get("status") == "completed"
    overridden = bool(session.get("score_overridden"))
    if not completed:
        official = False
    elif mark_mode == "ai_official":
        official = True
    else:  # professor_approved
        official = overridden
    session["mark_mode"] = mark_mode
    session["score_official"] = official
    session["score_status"] = "official" if official else "provisional"
