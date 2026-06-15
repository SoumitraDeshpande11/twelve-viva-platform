"""End-to-end API tests for the TWELVE viva backend (local provider, isolated SQLite)."""
from __future__ import annotations

import uuid

from app import main, storage

LONG_ANSWER = (
    "This comprehensive answer thoroughly covers correctness and implementation depth by "
    "describing B-tree indexing and hashing while explaining the tradeoffs in storage overhead "
    "and write amplification across the entire schema design in considerable detail for completeness."
)


# --- helpers ----------------------------------------------------------------

def bootstrap(c) -> str:
    r = c.post("/api/auth/bootstrap", json={"email": "admin@e.edu", "name": "Admin", "password": "longpassword12345"})
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


def create_exam(c, csrf, mark_mode="professor_approved", starts_at="", ends_at="", roll="23X001"):
    csv = f"roll_number,name,email\n{roll},Asha Rao,asha@e.edu\n".encode()
    return c.post(
        "/api/admin/exams",
        data={
            "name": "CSE Viva",
            "problem_statement": "Build an index",
            "curriculum": "DBMS",
            "rubric": "Correctness 50%, depth 50%",
            "mark_mode": mark_mode,
            "starts_at": starts_at,
            "ends_at": ends_at,
        },
        files={"student_csv": ("s.csv", csv, "text/csv")},
        headers={"X-CSRF-Token": csrf},
    )


def start_attempt(sc, exam_id, code, roll="23X001"):
    return sc.post(
        "/api/student/attempts/start",
        json={
            "exam_id": exam_id,
            "roll_number": roll,
            "one_time_code": code,
            "permissions": {"camera": True, "microphone": True, "fullscreen": True, "screen": False},
        },
    )


def complete_viva(sc, scsrf):
    for _ in range(15):
        cur = sc.get("/api/student/attempts/current").json()
        if cur.get("status") == "completed":
            return cur
        qid = cur.get("current_question_id")
        if not qid:
            break
        sc.post(
            "/api/student/attempts/current/answers",
            json={"question_id": qid, "answer_text": LONG_ANSWER, "input_mode": "typed"},
            headers={"X-CSRF-Token": scsrf, "Idempotency-Key": f"key-{qid}"},
        )
    return sc.get("/api/student/attempts/current").json()


def setup_completed_session(client, fresh_client, mark_mode="professor_approved"):
    csrf = bootstrap(client)
    exam = create_exam(client, csrf, mark_mode=mark_mode).json()
    exam_id = exam["id"]
    code = exam["students"][0]["token"]
    start = start_attempt(fresh_client, exam_id, code).json()
    session_id = start["id"]
    scsrf = start["csrf_token"]
    complete_viva(fresh_client, scsrf)
    return csrf, exam_id, session_id


# --- tests ------------------------------------------------------------------

def test_health(client):
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["ai_provider"] == "local"


def test_auth_gating_anonymous(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/admin/exams").status_code == 401
    assert client.get("/api/review/sessions").status_code == 401


def test_full_flow_completes_with_final_score(client, fresh_client):
    csrf, exam_id, session_id = setup_completed_session(client, fresh_client)
    review = client.get(f"/api/review/sessions/{session_id}").json()
    assert review["status"] == "completed"
    assert review["final_score"] is not None
    assert review["final_score"] > 0


def test_idempotent_answer_replay(client, fresh_client):
    csrf = bootstrap(client)
    exam = create_exam(client, csrf).json()
    start = start_attempt(fresh_client, exam["id"], exam["students"][0]["token"]).json()
    scsrf = start["csrf_token"]
    qid = start["current_question_id"]
    headers = {"X-CSRF-Token": scsrf, "Idempotency-Key": "dup-key"}
    body = {"question_id": qid, "answer_text": LONG_ANSWER, "input_mode": "typed"}
    first = fresh_client.post("/api/student/attempts/current/answers", json=body, headers=headers)
    second = fresh_client.post("/api/student/attempts/current/answers", json=body, headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200  # replay returns the session, not a duplicate


def test_mark_mode_ai_official_is_official_on_completion(client, fresh_client):
    csrf, exam_id, session_id = setup_completed_session(client, fresh_client, mark_mode="ai_official")
    review = client.get(f"/api/review/sessions/{session_id}").json()
    assert review["mark_mode"] == "ai_official"
    assert review["score_official"] is True
    assert review["score_status"] == "official"


def test_mark_mode_professor_approved_provisional_until_override(client, fresh_client):
    csrf, exam_id, session_id = setup_completed_session(client, fresh_client, mark_mode="professor_approved")
    review = client.get(f"/api/review/sessions/{session_id}").json()
    assert review["score_official"] is False
    assert review["score_status"] == "provisional"

    client.post(
        f"/api/review/sessions/{session_id}/override",
        json={"reviewer": "Prof", "override_score": 80, "reason": "approved"},
        headers={"X-CSRF-Token": csrf},
    )
    after = client.get(f"/api/review/sessions/{session_id}").json()
    assert after["score_official"] is True
    assert after["score_status"] == "official"


def test_override_applies_effective_score_without_mutating_final(client, fresh_client):
    csrf, exam_id, session_id = setup_completed_session(client, fresh_client)
    ai_score = client.get(f"/api/review/sessions/{session_id}").json()["final_score"]
    client.post(
        f"/api/review/sessions/{session_id}/override",
        json={"reviewer": "Prof", "override_score": 88, "reason": "strong viva"},
        headers={"X-CSRF-Token": csrf},
    )
    review = client.get(f"/api/review/sessions/{session_id}").json()
    assert review["final_score"] == ai_score  # AI audit value preserved
    assert review["effective_score"] == 88
    assert review["score_overridden"] is True


def test_override_reason_hidden_from_student(client, fresh_client):
    csrf, exam_id, session_id = setup_completed_session(client, fresh_client)
    client.post(
        f"/api/review/sessions/{session_id}/override",
        json={"reviewer": "Prof Secret", "override_score": 40, "reason": "suspected collusion"},
        headers={"X-CSRF-Token": csrf},
    )
    student_view = fresh_client.get("/api/student/attempts/current").json()
    assert student_view["effective_score"] == 40  # student sees their real grade
    assert student_view["override_reviewer"] is None  # but not who/why
    assert student_view["override_reason"] is None
    assert student_view["score_reviews"] == []

    staff_view = client.get(f"/api/review/sessions/{session_id}").json()
    assert staff_view["override_reviewer"] == "Prof Secret"
    assert staff_view["override_reason"] == "suspected collusion"


def test_window_future_exam_blocks_start(client, fresh_client):
    csrf = bootstrap(client)
    exam = create_exam(client, csrf, starts_at="2099-01-01T00:00:00+00:00", ends_at="2099-01-02T00:00:00+00:00").json()
    r = start_attempt(fresh_client, exam["id"], exam["students"][0]["token"])
    assert r.status_code == 403
    assert "not opened" in r.text


def test_window_closed_exam_blocks_start(client, fresh_client):
    csrf = bootstrap(client)
    exam = create_exam(client, csrf, starts_at="2000-01-01T00:00:00+00:00", ends_at="2000-01-02T00:00:00+00:00").json()
    r = start_attempt(fresh_client, exam["id"], exam["students"][0]["token"])
    assert r.status_code == 403
    assert "closed" in r.text


def test_window_validation_rejects_bad_order(client):
    csrf = bootstrap(client)
    r = create_exam(client, csrf, starts_at="2099-01-02T00:00:00+00:00", ends_at="2099-01-01T00:00:00+00:00")
    assert r.status_code == 400


def test_window_validation_accepts_mixed_offsets(client):
    csrf = bootstrap(client)
    # 20:00+05:30 = 14:30Z, before 16:00Z — a valid window despite raw-string ordering.
    r = create_exam(client, csrf, starts_at="2099-01-01T20:00:00+05:30", ends_at="2099-01-01T16:00:00+00:00")
    assert r.status_code == 200


def test_rescore_recovers_pending_ai_error(client, fresh_client, monkeypatch):
    csrf = bootstrap(client)
    exam = create_exam(client, csrf).json()
    start = start_attempt(fresh_client, exam["id"], exam["students"][0]["token"]).json()
    session_id = start["id"]
    scsrf = start["csrf_token"]
    qid = start["current_question_id"]

    # Force the AI scorer to fail like a real provider outage.
    # NB: restore by re-setattr, not monkeypatch.undo() — undo() would also revert the
    # client fixture's DB-path patches (same monkeypatch instance) and break auth.
    real_scorer = main.score_current_answer
    monkeypatch.setattr(
        main, "score_current_answer",
        lambda *a, **k: (main.pending_ai_error_result("provider down"), "openai", "provider down"),
    )
    fresh_client.post(
        "/api/student/attempts/current/answers",
        json={"question_id": qid, "answer_text": LONG_ANSWER, "input_mode": "typed"},
        headers={"X-CSRF-Token": scsrf, "Idempotency-Key": "k1"},
    )
    review = client.get(f"/api/review/sessions/{session_id}").json()
    answer = review["answers"][0]
    assert answer["scoring_status"] == "pending_ai_error"

    # Recover: provider healthy again, staff retries.
    monkeypatch.setattr(main, "score_current_answer", real_scorer)
    resc = client.post(
        f"/api/review/sessions/{session_id}/answers/{answer['id']}/rescore",
        headers={"X-CSRF-Token": csrf},
    )
    assert resc.status_code == 200
    assert resc.json()["answers"][0]["scoring_status"] == "scored"


def test_retranscribe_syncs_answer_text_and_rescores(client, fresh_client, monkeypatch):
    csrf = bootstrap(client)
    exam = create_exam(client, csrf).json()
    start = start_attempt(fresh_client, exam["id"], exam["students"][0]["token"]).json()
    session_id = start["id"]
    qid = start["current_question_id"]

    # Hand-craft a failed voice answer: stale answer_text + a pending audio submission linked by audio_ref.
    audio_path = storage.UPLOAD_DIR / "audio" / session_id / "a.webm"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake-audio")
    answer_id = str(uuid.uuid4())
    audio_id = str(uuid.uuid4())
    with storage.connect() as conn:
        conn.execute(
            "INSERT INTO answers (id, session_id, question_id, input_mode, answer_text, score, max_score, reasoning, audio_ref, created_at, scoring_status, scorer_provider, idempotency_key) "
            "VALUES (?, ?, ?, 'voice', '', 0, 10, 'AI scoring pending.', ?, ?, 'pending_ai_error', 'local', ?)",
            (answer_id, session_id, qid, str(audio_path), storage.utc_now(), str(uuid.uuid4())),
        )
        conn.execute(
            "INSERT INTO audio_submissions (id, session_id, question_id, storage_path, mime_type, size_bytes, transcription_status, transcription_provider, created_at) "
            "VALUES (?, ?, ?, ?, 'audio/webm', 9, 'pending_transcription_error', 'local', ?)",
            (audio_id, session_id, qid, str(audio_path), storage.utc_now()),
        )

    monkeypatch.setattr(
        main, "transcribe_audio",
        lambda *a, **k: {"status": "transcribed", "text": LONG_ANSWER, "provider": "local", "model": "test", "error": None},
    )
    r = client.post(
        f"/api/review/sessions/{session_id}/audio/{audio_id}/retranscribe",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200
    assert r.json()["rescored_answer_id"] == answer_id

    with storage.connect() as conn:
        row = conn.execute("SELECT answer_text, scoring_status FROM answers WHERE id = ?", (answer_id,)).fetchone()
    assert row["answer_text"] == LONG_ANSWER  # recovered transcript synced into the answer
    assert row["scoring_status"] == "scored"  # and re-scored off the new text


def test_override_tiebreak_list_matches_detail(client, fresh_client):
    csrf, exam_id, session_id = setup_completed_session(client, fresh_client)
    for score in (30, 70):
        client.post(
            f"/api/review/sessions/{session_id}/override",
            json={"reviewer": "Prof", "override_score": score, "reason": "r"},
            headers={"X-CSRF-Token": csrf},
        )
    detail = client.get(f"/api/review/sessions/{session_id}").json()
    listed = next(s for s in client.get("/api/review/sessions").json() if s["id"] == session_id)
    assert detail["effective_score"] == listed["effective_score"]


def test_delete_exam_cascades(client, fresh_client):
    csrf, exam_id, session_id = setup_completed_session(client, fresh_client)
    r = client.delete(f"/api/admin/exams/{exam_id}", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 204, r.text
    assert client.get(f"/api/admin/exams/{exam_id}").status_code == 404
    # Cascade removed the exam's session.
    with storage.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM viva_sessions WHERE id = ?", (session_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM students WHERE exam_id = ?", (exam_id,)).fetchone()[0] == 0


def test_delete_exam_requires_csrf_and_role(client):
    csrf = bootstrap(client)
    exam = create_exam(client, csrf).json()
    # Missing CSRF header is rejected.
    assert client.delete(f"/api/admin/exams/{exam['id']}").status_code == 403


def test_reset_attempt_issues_new_code_and_allows_restart(client, fresh_client):
    csrf = bootstrap(client)
    exam = create_exam(client, csrf).json()
    exam_id = exam["id"]
    student_id = exam["students"][0]["id"]
    old_code = exam["students"][0]["token"]
    # Use up the original attempt.
    start = start_attempt(fresh_client, exam_id, old_code).json()
    complete_viva(fresh_client, start["csrf_token"])
    # Old code is now spent.
    assert start_attempt(TestClientNew(), exam_id, old_code).status_code in (401, 409)

    r = client.post(
        f"/api/admin/exams/{exam_id}/students/{student_id}/reset-attempt",
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["student_id"] == student_id
    assert body["roll_number"] == "23X001"
    new_code = body["token"]
    assert new_code and new_code != old_code

    # Fresh code lets the student start over; old code stays invalid.
    again = start_attempt(TestClientNew(), exam_id, new_code)
    assert again.status_code == 200, again.text
    assert start_attempt(TestClientNew(), exam_id, old_code).status_code == 401


def test_finalize_excludes_pending_ai_error_from_mean(client, fresh_client, monkeypatch):
    """A pending_ai_error answer must not drag the student's final to a fake low score.

    With one good answer and one failed answer, the final reflects only the scored one.
    """
    csrf = bootstrap(client)
    exam = create_exam(client, csrf).json()
    start = start_attempt(fresh_client, exam["id"], exam["students"][0]["token"]).json()
    session_id = start["id"]
    scsrf = start["csrf_token"]

    # First answer scores normally.
    cur = fresh_client.get("/api/student/attempts/current").json()
    qid = cur["current_question_id"]
    fresh_client.post(
        "/api/student/attempts/current/answers",
        json={"question_id": qid, "answer_text": LONG_ANSWER, "input_mode": "typed"},
        headers={"X-CSRF-Token": scsrf, "Idempotency-Key": "good"},
    )

    # All remaining answers fail AI scoring.
    monkeypatch.setattr(
        main, "score_current_answer",
        lambda *a, **k: (main.pending_ai_error_result("provider down"), "openai", "provider down"),
    )
    completed = complete_viva(fresh_client, scsrf)
    assert completed["status"] in {"completed", "needs_review"}

    review = client.get(f"/api/review/sessions/{session_id}").json()
    scored = [a for a in review["answers"] if a["scoring_status"] == "scored"]
    pending = [a for a in review["answers"] if a["scoring_status"] == "pending_ai_error"]
    assert scored and pending  # mixed set
    if review["status"] == "completed":
        # final_score derived from scored answers only — the failed (0) answers excluded.
        earned = sum(a["score"] for a in scored)
        possible = sum(a["max_score"] for a in scored)
        expected = round((earned / possible) * 100, 1)
        assert review["final_score"] == expected


def test_finalize_all_pending_marks_needs_review(client, fresh_client, monkeypatch):
    """If every answer is pending_ai_error, the student gets needs_review, not a 0."""
    csrf = bootstrap(client)
    exam = create_exam(client, csrf).json()
    start = start_attempt(fresh_client, exam["id"], exam["students"][0]["token"]).json()
    session_id = start["id"]
    scsrf = start["csrf_token"]
    monkeypatch.setattr(
        main, "score_current_answer",
        lambda *a, **k: (main.pending_ai_error_result("provider down"), "openai", "provider down"),
    )
    complete_viva(fresh_client, scsrf)
    review = client.get(f"/api/review/sessions/{session_id}").json()
    assert review["status"] == "needs_review"
    assert review["final_score"] is None


def test_staff_cannot_post_answer_on_student_session(client, fresh_client):
    csrf = bootstrap(client)
    exam = create_exam(client, csrf).json()
    start = start_attempt(fresh_client, exam["id"], exam["students"][0]["token"]).json()
    session_id = start["id"]
    qid = start["current_question_id"]
    # Staff (legacy route) is blocked from driving a student's answer submission.
    r = client.post(
        f"/api/sessions/{session_id}/answer",
        json={"question_id": qid, "answer_text": LONG_ANSWER, "input_mode": "typed"},
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "staff-x"},
    )
    assert r.status_code == 403
    # But staff READ access still works.
    assert client.get(f"/api/sessions/{session_id}").status_code == 200


def test_login_rate_limit_locks_out(client):
    bootstrap(client)  # creates admin@e.edu
    for _ in range(5):
        assert client.post("/api/auth/login", json={"email": "admin@e.edu", "password": "wrong"}).status_code == 401
    # 6th attempt within the window is locked out even with a wrong password.
    assert client.post("/api/auth/login", json={"email": "admin@e.edu", "password": "wrong"}).status_code == 429
    # And a correct password is also locked out during the window.
    assert client.post("/api/auth/login", json={"email": "admin@e.edu", "password": "longpassword12345"}).status_code == 429


def test_staff_cannot_take_viva(client):
    csrf = bootstrap(client)
    exam = create_exam(client, csrf).json()
    code = exam["students"][0]["token"]
    # `client` carries the staff session cookie -> start must be refused.
    r = start_attempt(client, exam["id"], code)
    assert r.status_code == 403
    assert "Staff" in r.json()["detail"]


def test_create_staff_requires_super_admin_and_creates_loginable_user(client, fresh_client):
    csrf = bootstrap(client)
    # Anonymous (no staff cookie) cannot create staff.
    assert fresh_client.post("/api/auth/staff", json={
        "email": "x@e.edu", "name": "X", "password": "longpassword12345", "roles": ["examiner"],
    }).status_code in (401, 403)
    # super_admin can, with CSRF.
    r = client.post("/api/auth/staff", json={
        "email": "exam@e.edu", "name": "Examiner", "password": "longpassword12345", "roles": ["examiner"],
    }, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    assert r.json()["roles"] == ["examiner"]
    # The new account can log in.
    assert fresh_client.post("/api/auth/login", json={"email": "exam@e.edu", "password": "longpassword12345"}).status_code == 200


def test_create_staff_rejects_unknown_roles(client):
    csrf = bootstrap(client)
    r = client.post("/api/auth/staff", json={
        "email": "y@e.edu", "name": "Y", "password": "longpassword12345", "roles": ["wizard"],
    }, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400


def test_bootstrap_requires_token_when_configured(client, monkeypatch):
    monkeypatch.setenv("TWELVE_BOOTSTRAP_TOKEN", "s3cret-setup-token")
    # Wrong/missing token -> rejected even on a fresh (no-user) DB.
    assert client.post("/api/auth/bootstrap", json={
        "email": "admin@e.edu", "name": "Admin", "password": "longpassword12345",
    }).status_code == 403
    # Correct token -> succeeds.
    assert client.post("/api/auth/bootstrap", json={
        "email": "admin@e.edu", "name": "Admin", "password": "longpassword12345",
        "bootstrap_token": "s3cret-setup-token",
    }).status_code == 200


def TestClientNew():
    """A fresh TestClient (separate cookie jar) for an anonymous student start."""
    from fastapi.testclient import TestClient

    return TestClient(main.app)


# --- logout / session switching (auth-login-logout-spec) ---------------------

def audit_events(event_type):
    with main.connect() as conn:
        return [main.row_to_dict(r) for r in conn.execute(
            "SELECT * FROM security_audit_events WHERE event_type = ? ORDER BY created_at", (event_type,)
        )]


def test_student_logout_clears_session_and_audits(client, fresh_client):
    csrf = bootstrap(client)
    exam = create_exam(client, csrf).json()
    start = start_attempt(fresh_client, exam["id"], exam["students"][0]["token"]).json()
    scsrf = start["csrf_token"]
    # An active student attempt is visible via me.
    assert fresh_client.get("/api/auth/me").json()["role"] == "student"
    r = fresh_client.post("/api/auth/logout", headers={"X-CSRF-Token": scsrf})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "role": "student"}
    # Session row is gone: me now 401, and the attempt requires a fresh code to restart.
    assert fresh_client.get("/api/auth/me").status_code == 401
    events = audit_events("student_logout")
    assert len(events) == 1
    assert events[0]["actor_type"] == "student"


def test_staff_logout_audits(client):
    csrf = bootstrap(client)
    r = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "staff"
    assert client.get("/api/auth/me").status_code == 401
    events = audit_events("staff_logout")
    assert len(events) == 1
    assert events[0]["actor_type"] == "staff"


def test_transcription_falls_back_to_draft_on_provider_error(monkeypatch):
    """A transient provider failure (e.g. 503) degrades to the browser draft in dev,
    instead of failing the answer."""
    from pathlib import Path
    from app import transcription as t

    monkeypatch.setenv("TWELVE_TRANSCRIPTION_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "x")

    def boom(*_a, **_k):
        raise t.TranscriptionError("Server error '503 Service Unavailable'")

    monkeypatch.setattr(t, "transcribe_with_gemini", boom)
    out = t.transcribe_audio(Path("x.wav"), "audio/wav", draft_transcript="hello world")
    assert out["status"] == "draft_used"
    assert out["text"] == "hello world"
    assert "failed-local-fallback" in out["provider"]
    assert "503" in out["error"]


def test_transcription_falls_back_without_draft_in_dev(monkeypatch):
    """A provider 429 with no browser draft still degrades locally (empty text) rather
    than surfacing the raw provider error to the student."""
    from pathlib import Path
    from app import transcription as t

    monkeypatch.setenv("TWELVE_TRANSCRIPTION_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "x")

    def boom(*_a, **_k):
        raise t.TranscriptionError("Client error '429 Too Many Requests'")

    monkeypatch.setattr(t, "transcribe_with_gemini", boom)
    out = t.transcribe_audio(Path("x.wav"), "audio/wav", draft_transcript=None)
    assert out["status"] == "draft_used"
    assert out["text"] == ""
    assert "failed-local-fallback" in out["provider"]
    assert "429" in out["error"]


def test_transcription_reraises_provider_error_in_prod(monkeypatch):
    """In staging/prod a provider failure must NOT silently substitute a draft."""
    from pathlib import Path
    from app import transcription as t

    monkeypatch.setenv("TWELVE_TRANSCRIPTION_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setenv("TWELVE_ENV", "production")

    def boom(*_a, **_k):
        raise t.TranscriptionError("503")

    monkeypatch.setattr(t, "transcribe_with_gemini", boom)
    import pytest as _pytest
    with _pytest.raises(t.TranscriptionError):
        t.transcribe_audio(Path("x.wav"), "audio/wav", draft_transcript="hello")


# --- viva video recording (viva-video-recording-spec) ------------------------

def upload_recording(sc, scsrf, data=b"fake-webm-bytes"):
    return sc.post(
        "/api/student/attempts/current/recording",
        files={"recording": ("viva.webm", data, "video/webm")},
        headers={"X-CSRF-Token": scsrf},
    )


def test_upload_recording_stores_row_file_and_review_can_play(client, fresh_client):
    from pathlib import Path

    csrf = bootstrap(client)
    exam = create_exam(client, csrf).json()
    start = start_attempt(fresh_client, exam["id"], exam["students"][0]["token"]).json()
    session_id = start["id"]
    r = upload_recording(fresh_client, start["csrf_token"])
    assert r.status_code == 200, r.text
    rid = r.json()["recording_id"]

    with main.connect() as conn:
        row = main.row_to_dict(
            conn.execute("SELECT * FROM session_recordings WHERE id = ?", (rid,)).fetchone()
        )
    assert row and row["session_id"] == session_id
    assert Path(row["storage_path"]).exists()

    # Review detail exposes the recording, and staff can stream it back.
    detail = client.get(f"/api/review/sessions/{session_id}").json()
    assert any(rec["id"] == rid for rec in detail["recordings"])
    got = client.get("/api/review/recording", params={"ref": row["storage_path"]})
    assert got.status_code == 200
    assert got.headers["content-type"].startswith("video/webm")


def test_upload_recording_rejects_too_large(client, fresh_client, monkeypatch):
    monkeypatch.setenv("TWELVE_MAX_VIDEO_BYTES", "10")
    csrf = bootstrap(client)
    exam = create_exam(client, csrf).json()
    start = start_attempt(fresh_client, exam["id"], exam["students"][0]["token"]).json()
    r = upload_recording(fresh_client, start["csrf_token"], data=b"x" * 100)
    assert r.status_code == 413


def test_upload_recording_rejected_when_not_active(client, fresh_client):
    csrf, exam_id, session_id = setup_completed_session(client, fresh_client)
    scsrf = fresh_client.get("/api/auth/me").json()["csrf_token"]
    r = upload_recording(fresh_client, scsrf)
    assert r.status_code == 409


def test_review_recording_rejects_out_of_jail_and_missing(client):
    bootstrap(client)
    # A ref outside UPLOAD_DIR is refused outright.
    assert client.get("/api/review/recording", params={"ref": "/etc/passwd"}).status_code == 403
    # A ref inside UPLOAD_DIR but with no file is a 404.
    missing = str(main.UPLOAD_DIR / "recordings" / "nope" / "x.webm")
    assert client.get("/api/review/recording", params={"ref": missing}).status_code == 404


def test_review_recording_requires_staff(client, fresh_client):
    csrf = bootstrap(client)
    exam = create_exam(client, csrf).json()
    start = start_attempt(fresh_client, exam["id"], exam["students"][0]["token"]).json()
    upload_recording(fresh_client, start["csrf_token"])
    with main.connect() as conn:
        ref = conn.execute("SELECT storage_path FROM session_recordings LIMIT 1").fetchone()[0]
    # The student (fresh_client) must not be able to stream recordings.
    assert fresh_client.get("/api/review/recording", params={"ref": ref}).status_code in (401, 403)


def test_exam_delete_removes_recording_files(client, fresh_client):
    from pathlib import Path

    csrf = bootstrap(client)
    exam = create_exam(client, csrf).json()
    exam_id = exam["id"]
    start = start_attempt(fresh_client, exam_id, exam["students"][0]["token"]).json()
    upload_recording(fresh_client, start["csrf_token"])
    with main.connect() as conn:
        path = conn.execute("SELECT storage_path FROM session_recordings LIMIT 1").fetchone()[0]
    assert Path(path).exists()

    assert client.delete(f"/api/admin/exams/{exam_id}", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert not Path(path).exists()
    with main.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM session_recordings").fetchone()[0] == 0


# --- local providers: Ollama LLM + faster-whisper STT ------------------------

EXAM_STUB = {"name": "E", "problem_statement": "p", "curriculum": "c", "rubric": "Correctness 50, depth 50"}
Q_STUB = {"category": "core-subject", "text": "q", "expected_points": ["x"]}


def test_ollama_provider_scores_via_dispatcher(monkeypatch):
    monkeypatch.setenv("TWELVE_AI_PROVIDER", "ollama")
    monkeypatch.setattr(
        main, "score_answer_with_ollama",
        lambda *a, **k: {"score": 7.0, "max_score": 10.0, "reasoning": "solid"},
    )
    result, provider, err = main.score_current_answer(Q_STUB, "an answer", EXAM_STUB)
    assert provider == "ollama"
    assert result["score"] == 7.0 and result["status"] == "scored"
    assert err is None


def test_ollama_error_falls_back_to_local(monkeypatch):
    monkeypatch.setenv("TWELVE_AI_PROVIDER", "ollama")

    def boom(*_a, **_k):
        raise main.OllamaAgentError("ollama down")

    monkeypatch.setattr(main, "score_answer_with_ollama", boom)
    result, provider, err = main.score_current_answer(Q_STUB, LONG_ANSWER, EXAM_STUB)
    assert provider == "local-fallback"
    assert result["status"] == "scored"
    assert "ollama down" in err


def test_whisper_transcription_via_dispatcher(monkeypatch):
    from pathlib import Path
    from app import transcription as t

    monkeypatch.setenv("TWELVE_TRANSCRIPTION_PROVIDER", "whisper")
    monkeypatch.setattr(t, "whisper_transcription_configured", lambda: True)
    monkeypatch.setattr(
        t, "transcribe_with_whisper",
        lambda _p: {"status": "transcribed", "text": "hello there", "provider": "faster-whisper", "model": "base", "error": None},
    )
    out = t.transcribe_audio(Path("x.webm"), "audio/webm")
    assert out["provider"] == "faster-whisper"
    assert out["text"] == "hello there"


def test_whisper_error_falls_back_to_draft(monkeypatch):
    from pathlib import Path
    from app import transcription as t

    monkeypatch.setenv("TWELVE_TRANSCRIPTION_PROVIDER", "whisper")
    monkeypatch.setattr(t, "whisper_transcription_configured", lambda: True)

    def boom(_p):
        raise t.TranscriptionError("whisper boom")

    monkeypatch.setattr(t, "transcribe_with_whisper", boom)
    out = t.transcribe_audio(Path("x.webm"), "audio/webm", draft_transcript="draft text")
    assert out["status"] == "draft_used"
    assert out["text"] == "draft text"
    assert "whisper-failed-local-fallback" in out["provider"]


def test_logout_without_session_is_noop(client):
    # No cookie -> role is null, no audit row, still clears cookies cleanly.
    r = client.post("/api/auth/logout")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "role": None}
    assert audit_events("staff_logout") == []
    assert audit_events("student_logout") == []
