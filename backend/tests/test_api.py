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
