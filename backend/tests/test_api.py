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
            # All questions answered; the viva is no longer auto-finalized — the student
            # explicitly submits it for review.
            sc.post(f"/api/sessions/{cur['id']}/finalize", headers={"X-CSRF-Token": scsrf})
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


def test_create_staff_requires_staff_and_creates_loginable_user(client, fresh_client):
    csrf = bootstrap(client)
    # Anonymous (no staff cookie) cannot create staff.
    assert fresh_client.post("/api/auth/staff", json={
        "email": "x@e.edu", "name": "X", "password": "longpassword12345", "roles": ["examiner"],
    }).status_code in (401, 403)
    # Any signed-in staff can, with CSRF.
    r = client.post("/api/auth/staff", json={
        "email": "exam@e.edu", "name": "Examiner", "password": "longpassword12345", "roles": ["examiner"],
    }, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    assert r.json()["roles"] == ["examiner"]
    # The new account can log in.
    assert fresh_client.post("/api/auth/login", json={"email": "exam@e.edu", "password": "longpassword12345"}).status_code == 200


def _login(c, email, password="longpassword12345"):
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["csrf_token"]


def test_non_superadmin_staff_cannot_manage_staff(client, fresh_client):
    csrf = bootstrap(client)
    # Create a plain examiner, then log in as them in a fresh jar.
    client.post("/api/auth/staff", json={
        "email": "ex@e.edu", "name": "Ex", "password": "longpassword12345", "roles": ["examiner"],
    }, headers={"X-CSRF-Token": csrf})
    ex_csrf = _login(fresh_client, "ex@e.edu")
    # Account/role management stays super_admin only: examiner is rejected on every staff route.
    assert fresh_client.get("/api/auth/staff").status_code == 403
    assert fresh_client.post("/api/auth/staff", json={
        "email": "inv@e.edu", "name": "Inv", "password": "longpassword12345", "roles": ["invigilator"],
    }, headers={"X-CSRF-Token": ex_csrf}).status_code == 403


def test_list_staff_returns_directory(client):
    csrf = bootstrap(client)
    client.post("/api/auth/staff", json={
        "email": "ex@e.edu", "name": "Ex", "password": "longpassword12345", "roles": ["examiner"],
    }, headers={"X-CSRF-Token": csrf})
    rows = client.get("/api/auth/staff").json()
    assert len(rows) == 2
    examiner = next(r for r in rows if r["email"] == "ex@e.edu")
    assert examiner["roles"] == ["examiner"]
    assert examiner["active"] is True


def test_update_staff_changes_roles_and_deactivates(client, fresh_client):
    csrf = bootstrap(client)
    created = client.post("/api/auth/staff", json={
        "email": "ex@e.edu", "name": "Ex", "password": "longpassword12345", "roles": ["examiner"],
    }, headers={"X-CSRF-Token": csrf}).json()
    uid = created["id"]
    # Change roles.
    r = client.patch(f"/api/auth/staff/{uid}", json={"roles": ["exam_admin", "invigilator"]}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    assert sorted(r.json()["roles"]) == ["exam_admin", "invigilator"]
    # Deactivate -> the account can no longer log in, and live sessions are revoked.
    assert fresh_client.post("/api/auth/login", json={"email": "ex@e.edu", "password": "longpassword12345"}).status_code == 200
    r = client.patch(f"/api/auth/staff/{uid}", json={"active": False}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and r.json()["active"] is False
    assert fresh_client.post("/api/auth/login", json={"email": "ex@e.edu", "password": "longpassword12345"}).status_code == 401


def test_update_staff_cannot_remove_last_super_admin(client):
    csrf = bootstrap(client)  # the bootstrap user is the only super_admin
    me = client.get("/api/auth/me").json()
    uid = me["user"]["id"]
    # Demoting the last super admin is blocked.
    assert client.patch(f"/api/auth/staff/{uid}", json={"roles": ["examiner"]}, headers={"X-CSRF-Token": csrf}).status_code == 400
    # Self-deactivation is blocked.
    assert client.patch(f"/api/auth/staff/{uid}", json={"active": False}, headers={"X-CSRF-Token": csrf}).status_code == 400


def test_update_staff_requires_staff_and_exists(client, fresh_client):
    csrf = bootstrap(client)
    assert fresh_client.patch("/api/auth/staff/whoever", json={"active": False}).status_code in (401, 403)
    assert client.patch("/api/auth/staff/missing", json={"active": False}, headers={"X-CSRF-Token": csrf}).status_code == 404


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


def test_upload_recording_accepted_when_completed(client, fresh_client):
    # The full-viva recording is flushed at the END of the viva, by which point the
    # session is already `completed`. That is the normal upload path, so it must succeed
    # (regression: an active-only check previously 409'd every real recording upload).
    csrf, exam_id, session_id = setup_completed_session(client, fresh_client)
    scsrf = fresh_client.get("/api/auth/me").json()["csrf_token"]
    r = upload_recording(fresh_client, scsrf)
    assert r.status_code == 200, r.text
    with main.connect() as conn:
        row = conn.execute(
            "SELECT session_id FROM session_recordings WHERE id = ?", (r.json()["recording_id"],)
        ).fetchone()
    assert row and row[0] == session_id


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


# --- AI provider health / degraded-mode signalling --------------------------

def _reset_ai_health():
    main._ai_health.update(degraded=False, since=None, last_error=None, _last_probe=0.0)


def test_ai_health_reports_full_by_default(client):
    _reset_ai_health()
    body = client.get("/api/ai/health").json()
    assert body["degraded"] is False
    assert body["mode"] == "full"


def test_ai_health_degrades_on_fallback_and_recovers(client, monkeypatch):
    _reset_ai_health()
    # A dispatcher returning the local fallback means the configured provider failed.
    main.note_provider_outcome("local-fallback", "gemini boom")
    body = client.get("/api/ai/health").json()
    assert body["degraded"] is True
    assert body["mode"] == "local-fallback"
    assert body["since"]

    # The heartbeat endpoint re-probes the provider; once it returns healthy, the flag clears.
    monkeypatch.setattr(main, "probe_active_provider", lambda: True)
    main._ai_health["_last_probe"] = 0.0  # bypass the probe throttle for the test
    body2 = client.get("/api/ai/health").json()
    assert body2["degraded"] is False
    assert body2["mode"] == "full"


def test_ai_health_stays_degraded_while_provider_down(client, monkeypatch):
    _reset_ai_health()
    main.note_provider_outcome("local-fallback", "still down")
    monkeypatch.setattr(main, "probe_active_provider", lambda: False)
    main._ai_health["_last_probe"] = 0.0
    assert client.get("/api/ai/health").json()["degraded"] is True


def test_note_provider_outcome_ignores_plain_local(client):
    # A provider intentionally set to local has nothing to degrade.
    _reset_ai_health()
    main.note_provider_outcome("local", None)
    assert client.get("/api/ai/health").json()["degraded"] is False


# --- exam editing -----------------------------------------------------------

def test_update_exam_edits_fields(client):
    csrf = bootstrap(client)
    exam = create_exam(client, csrf).json()
    r = client.patch(
        f"/api/admin/exams/{exam['id']}",
        json={"name": "Renamed Viva", "rubric": "New rubric weights", "mark_mode": "ai_official"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Renamed Viva"
    assert body["rubric"] == "New rubric weights"
    assert body["mark_mode"] == "ai_official"
    # Untouched field preserved.
    assert body["problem_statement"] == exam["problem_statement"]


def test_update_exam_rejects_blank_and_bad_mark_mode(client):
    csrf = bootstrap(client)
    exam = create_exam(client, csrf).json()
    assert client.patch(
        f"/api/admin/exams/{exam['id']}", json={"name": "   "}, headers={"X-CSRF-Token": csrf}
    ).status_code == 400
    assert client.patch(
        f"/api/admin/exams/{exam['id']}", json={"mark_mode": "nonsense"}, headers={"X-CSRF-Token": csrf}
    ).status_code == 400


def test_update_exam_rejects_inverted_window(client):
    csrf = bootstrap(client)
    exam = create_exam(client, csrf).json()
    r = client.patch(
        f"/api/admin/exams/{exam['id']}",
        json={"starts_at": "2030-01-02T10:00", "ends_at": "2030-01-01T10:00"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 400


def test_update_exam_requires_staff_and_exists(client, fresh_client):
    csrf = bootstrap(client)
    exam = create_exam(client, csrf).json()
    # Anonymous client cannot edit.
    assert fresh_client.patch(
        f"/api/admin/exams/{exam['id']}", json={"name": "x"}
    ).status_code in (401, 403)
    # Missing exam → 404.
    assert client.patch(
        "/api/admin/exams/does-not-exist", json={"name": "x"}, headers={"X-CSRF-Token": csrf}
    ).status_code == 404


# --- exam archiving, assignment, access scoping -----------------------------

def test_archive_hides_exam_from_default_list_and_unarchive_restores(client):
    csrf = bootstrap(client)
    exam = create_exam(client, csrf).json()
    eid = exam["id"]
    assert client.post(f"/api/admin/exams/{eid}/archive", headers={"X-CSRF-Token": csrf}).status_code == 200
    # Default admin list excludes archived; include_archived shows it.
    assert eid not in [e["id"] for e in client.get("/api/admin/exams").json()]
    assert eid in [e["id"] for e in client.get("/api/admin/exams?include_archived=true").json()]
    # Default review list also excludes it.
    assert eid not in [e["id"] for e in client.get("/api/review/exams").json()]
    # Unarchive restores.
    assert client.post(f"/api/admin/exams/{eid}/unarchive", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert eid in [e["id"] for e in client.get("/api/admin/exams").json()]


def test_review_exams_reports_taken_counts(client, fresh_client):
    csrf, exam_id, session_id = setup_completed_session(client, fresh_client)
    row = next(e for e in client.get("/api/review/exams").json() if e["id"] == exam_id)
    assert row["student_count"] == 1
    assert row["taken_count"] == 1
    assert row["completed_count"] == 1


def test_review_class_lists_all_students_including_not_started(client, fresh_client):
    csrf = bootstrap(client)
    # Two students; only one takes the viva.
    csv = b"roll_number,name,email\n23X001,Asha,a@e.edu\n23X002,Vikram,v@e.edu\n"
    exam = client.post(
        "/api/admin/exams",
        data={"name": "Class Exam", "problem_statement": "p", "curriculum": "c", "rubric": "Correctness 100%", "mark_mode": "professor_approved"},
        files={"student_csv": ("s.csv", csv, "text/csv")},
        headers={"X-CSRF-Token": csrf},
    ).json()
    code = next(s["token"] for s in exam["students"] if s["roll_number"] == "23X001")
    start = start_attempt(fresh_client, exam["id"], code, roll="23X001").json()
    complete_viva(fresh_client, start["csrf_token"])

    body = client.get(f"/api/review/exams/{exam['id']}/class").json()
    assert body["student_count"] == 2
    assert body["taken_count"] == 1
    statuses = {r["roll_number"]: r["attempt_status"] for r in body["roster"]}
    assert statuses["23X001"] == "completed"
    assert statuses["23X002"] == "not_started"


def test_assignment_scopes_examiner_to_assigned_exams(client, fresh_client):
    csrf = bootstrap(client)
    exam_a = create_exam(client, csrf, roll="23A001").json()
    exam_b = create_exam(client, csrf, roll="23B001").json()
    # Create an examiner and log in (fresh jar).
    examiner = client.post("/api/auth/staff", json={
        "email": "ex@e.edu", "name": "Ex", "password": "longpassword12345", "roles": ["examiner"],
    }, headers={"X-CSRF-Token": csrf}).json()
    _login(fresh_client, "ex@e.edu")

    # Unassigned examiner sees no exams in review and is blocked from exam B's class.
    assert fresh_client.get("/api/review/exams").json() == []
    assert fresh_client.get(f"/api/review/exams/{exam_b['id']}/class").status_code == 403

    # Assign examiner to exam A only.
    r = client.post(f"/api/admin/exams/{exam_a['id']}/assignments", json={"user_id": examiner["id"]}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert examiner["id"] in [a["id"] for a in r.json()]

    seen = [e["id"] for e in fresh_client.get("/api/review/exams").json()]
    assert seen == [exam_a["id"]]
    assert fresh_client.get(f"/api/review/exams/{exam_a['id']}/class").status_code == 200
    assert fresh_client.get(f"/api/review/exams/{exam_b['id']}/class").status_code == 403


def test_admin_sees_all_exams_regardless_of_assignment(client):
    csrf = bootstrap(client)
    create_exam(client, csrf, roll="23A001")
    create_exam(client, csrf, roll="23B001")
    # The bootstrap super_admin sees all exams in review without any assignment.
    assert len(client.get("/api/review/exams").json()) == 2


def test_assign_unassign_and_assignable_staff(client):
    csrf = bootstrap(client)
    exam = create_exam(client, csrf).json()
    staff = client.post("/api/auth/staff", json={
        "email": "ex@e.edu", "name": "Ex", "password": "longpassword12345", "roles": ["examiner"],
    }, headers={"X-CSRF-Token": csrf}).json()
    assert staff["id"] in [s["id"] for s in client.get("/api/admin/assignable-staff").json()]
    client.post(f"/api/admin/exams/{exam['id']}/assignments", json={"user_id": staff["id"]}, headers={"X-CSRF-Token": csrf})
    assert client.get(f"/api/admin/exams/{exam['id']}/assignments").json()[0]["id"] == staff["id"]
    r = client.request("DELETE", f"/api/admin/exams/{exam['id']}/assignments/{staff['id']}", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and r.json() == []


# --- AI provider failover ---------------------------------------------------

def _raise(exc):
    def _fn(*a, **k):
        raise exc
    return _fn


def test_score_fails_over_gemini_to_ollama(monkeypatch):
    monkeypatch.setenv("TWELVE_AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "x")  # makes gemini_configured() true
    monkeypatch.setattr(main, "score_answer_with_gemini", _raise(main.GeminiAgentError("429 Too Many Requests")))
    monkeypatch.setattr(main, "score_answer_with_ollama", lambda *a, **k: {"score": 9.0, "max_score": 10.0, "reasoning": "ok"})
    q = {"category": "design", "text": "Q", "expected_points": []}
    exam = {"name": "E", "problem_statement": "p", "curriculum": "c", "rubric": "r"}
    result, provider, err = main.score_current_answer(q, "answer", exam)
    assert provider == "ollama"          # auto-failed over, not local
    assert result["score"] == 9.0
    assert err and "gemini" in err       # the gemini failure is recorded


def test_score_failover_to_local_when_all_ai_fail(monkeypatch):
    monkeypatch.setenv("TWELVE_AI_PROVIDER", "ollama")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(main, "score_answer_with_ollama", _raise(main.OllamaAgentError("connection refused")))
    q = {"category": "design", "text": "Q", "expected_points": []}
    exam = {"name": "E", "problem_statement": "p", "curriculum": "c", "rubric": "r"}
    result, provider, err = main.score_current_answer(q, "answer", exam)
    assert provider == "local-fallback"  # only after every AI failed
    assert err and "ollama" in err
