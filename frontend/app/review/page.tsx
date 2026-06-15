"use client";

import { FormEvent, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, RefreshCcw, Save, ShieldAlert } from "lucide-react";
import { API_BASE, api, VivaSession } from "../../lib/api";
import { AuthPanel } from "../AuthPanel";

type ScoreReview = {
  id: string;
  reviewer: string;
  override_score: number;
  reason: string;
  created_at: string;
};

type ReviewSession = VivaSession & {
  score_reviews?: ScoreReview[];
  rubric?: string;
};

export default function ReviewPage() {
  const [sessions, setSessions] = useState<VivaSession[]>([]);
  const [selected, setSelected] = useState<ReviewSession | null>(null);
  const [message, setMessage] = useState("");
  const [needsAuth, setNeedsAuth] = useState(false);
  const [tab, setTab] = useState<"summary" | "answers" | "proctoring" | "transcript">("summary");

  async function loadSessions() {
    const data = await api<VivaSession[]>("/api/review/sessions");
    setSessions(data);
    if (data[0]) {
      setSelected(await api<ReviewSession>(`/api/review/sessions/${data[0].id}`));
    }
  }

  useEffect(() => {
    api("/api/auth/me").then(() => loadSessions()).catch(() => setNeedsAuth(true));
  }, []);

  async function selectSession(sessionId: string) {
    setSelected(await api<ReviewSession>(`/api/review/sessions/${sessionId}`));
    setTab("summary");
  }

  async function submitOverride(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) {
      return;
    }
    const form = new FormData(event.currentTarget);
    await api<ScoreReview>(`/api/review/sessions/${selected.id}/override`, {
      method: "POST",
      body: JSON.stringify({
        reviewer: form.get("reviewer"),
        override_score: Number(form.get("override_score")),
        reason: form.get("reason")
      })
    });
    event.currentTarget.reset();
    setSelected(await api<ReviewSession>(`/api/review/sessions/${selected.id}`));
  }

  async function rescoreAnswer(answerId: string) {
    if (!selected) {
      return;
    }
    setSelected(await api<ReviewSession>(`/api/review/sessions/${selected.id}/answers/${answerId}/rescore`, { method: "POST" }));
  }

  async function retranscribeAudio(audioId: string) {
    if (!selected) {
      return;
    }
    await api(`/api/review/sessions/${selected.id}/audio/${audioId}/retranscribe`, { method: "POST" });
    setSelected(await api<ReviewSession>(`/api/review/sessions/${selected.id}`));
  }

  if (needsAuth) {
    return <AuthPanel onReady={() => { setNeedsAuth(false); loadSessions().catch((error) => setMessage(error.message)); }} />;
  }

  return (
    <main className="shell">
      <div className="page-head">
        <div>
          <h1>Professor Review</h1>
          <p>Review the viva transcript, score reasoning, proctoring flags, and any score overrides without mutating the original AI score.</p>
        </div>
        <button className="secondary" type="button" onClick={() => loadSessions()}>
          <RefreshCcw size={17} /> Refresh
        </button>
      </div>

      <section className="grid two">
        <aside className="panel">
          <h2 className="section-title">Sessions</h2>
          <div className="grid">
            {sessions.map((session) => (
              <button key={session.id} className={selected?.id === session.id ? "secondary" : ""} onClick={() => selectSession(session.id)} type="button">
                <strong>{session.student_name}</strong>
                <span className="muted small">
                  {session.roll_number} · {session.status} · {session.effective_score ?? session.final_score ?? "pending"}%{session.score_overridden ? " (override)" : ""}
                </span>
                <span className={session.proctoring_count ? "status warn" : "status ok"}>
                  <ShieldAlert size={15} /> {session.proctoring_count ?? 0} flags
                </span>
              </button>
            ))}
            {!sessions.length && <p className="muted small">No viva sessions yet.</p>}
          </div>
          {message && <p className="muted small">{message}</p>}
        </aside>

        {selected && (
          <section className="grid">
            <div className="panel">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <div>
                  <h2 className="section-title">{selected.student_name}</h2>
                  <p className="muted small">{selected.exam_name} · {selected.roll_number}</p>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div className="score">{selected.effective_score ?? selected.final_score ?? 0}%</div>
                  {selected.score_overridden ? (
                    <p className="muted small">
                      Professor override{selected.override_reviewer ? ` · ${selected.override_reviewer}` : ""} · AI {selected.final_score ?? 0}%
                    </p>
                  ) : (
                    <p className="muted small">AI score</p>
                  )}
                  <span className={selected.score_official ? "status ok" : "status warn"}>
                    {selected.score_status ?? "provisional"}{selected.mark_mode === "ai_official" ? " · AI official" : ""}
                  </span>
                </div>
              </div>
              <div className="row">
                <span className={selected.status === "completed" ? "status ok" : "status warn"}>
                  {selected.status === "completed" ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
                  {selected.status}
                </span>
                <span className="status">Started {new Date(selected.started_at).toLocaleString()}</span>
              </div>
              <div className="tabs" style={{ marginTop: 14 }}>
                <button className={tab === "summary" ? "secondary" : ""} onClick={() => setTab("summary")} type="button">Summary</button>
                <button className={tab === "answers" ? "secondary" : ""} onClick={() => setTab("answers")} type="button">Answers</button>
                <button className={tab === "proctoring" ? "secondary" : ""} onClick={() => setTab("proctoring")} type="button">Proctoring</button>
                <button className={tab === "transcript" ? "secondary" : ""} onClick={() => setTab("transcript")} type="button">Transcript</button>
              </div>
            </div>

            {tab === "summary" && (
              <div className="panel">
                <h2 className="section-title">Summary</h2>
                <div className="row">
                  <span className="status">Answers {selected.answers.length}/{selected.questions.length}</span>
                  <span className="status">Flags {selected.proctoring_events.length}</span>
                  <span className="status">Mode {selected.status}</span>
                </div>
                {selected.rubric && <pre className="small">{selected.rubric}</pre>}
              </div>
            )}

            {tab === "answers" && (
            <div className="panel">
              <h2 className="section-title">Answers And Scores</h2>
              <div className="timeline">
                {selected.questions.map((question) => {
                  const answer = selected.answers.find((item) => item.question_id === question.id);
                  return (
                    <div className="timeline-item" key={question.id}>
                      <strong>Q{question.ordinal}. {question.text}</strong>
                      <div className="muted small">{question.category}</div>
                      {answer ? (
                        <>
                          <p>{answer.answer_text}</p>
                          <p className="muted small">
                            Score {answer.score}/{answer.max_score} · {answer.scoring_status ?? "scored"} · {answer.scorer_provider ?? "unknown"}
                          </p>
                          {answer.scoring_status === "pending_ai_error" && (
                            <button type="button" onClick={() => rescoreAnswer(answer.id).catch((error) => setMessage(error.message))}>
                              <RefreshCcw size={14} /> Retry AI scoring
                            </button>
                          )}
                          <p className="muted small">{answer.reasoning}</p>
                          {answer.audio_ref && <audio controls src={`${API_BASE}/api/review/audio?ref=${encodeURIComponent(answer.audio_ref)}`} />}
                          {answer.audio_ref && selected.audio_submissions?.filter((audio) => audio.audio_ref === answer.audio_ref).map((audio) => (
                            <div className="muted small" key={audio.id}>
                              Transcript {audio.transcription_status} · {audio.transcription_provider}{audio.transcription_model ? ` · ${audio.transcription_model}` : ""}
                              {audio.transcription_status === "pending_transcription_error" && (
                                <button type="button" style={{ marginLeft: 8 }} onClick={() => retranscribeAudio(audio.id).catch((error) => setMessage(error.message))}>
                                  <RefreshCcw size={14} /> Retry transcription
                                </button>
                              )}
                            </div>
                          ))}
                        </>
                      ) : (
                        <p className="muted small">No answer recorded.</p>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
            )}
          </section>
        )}
      </section>

      {selected && tab === "proctoring" && (
        <section className="grid two" style={{ marginTop: 16 }}>
          <div className="panel">
            <h2 className="section-title">Proctoring Timeline</h2>
            <div className="timeline">
              {selected.proctoring_events.map((event) => (
                <div className="timeline-item flag" key={event.id}>
                  <strong>{event.event_type}</strong>
                  <div className="muted small">
                    {new Date(event.created_at).toLocaleString()} · {event.severity ?? "warning"} · confidence {Math.round(event.confidence * 100)}%
                  </div>
                  <pre className="small">{JSON.stringify(event.details, null, 2)}</pre>
                </div>
              ))}
              {!selected.proctoring_events.length && <p className="muted small">No proctoring flags were logged.</p>}
            </div>
          </div>

          <div className="panel">
            <h2 className="section-title">Score Override</h2>
            <form onSubmit={submitOverride}>
              <div className="field">
                <label htmlFor="reviewer">Reviewer</label>
                <input id="reviewer" name="reviewer" required placeholder="Prof. name" />
              </div>
              <div className="field">
                <label htmlFor="override_score">Override score</label>
                <input id="override_score" name="override_score" required type="number" min="0" max="100" step="0.1" />
              </div>
              <div className="field">
                <label htmlFor="reason">Reason</label>
                <textarea id="reason" name="reason" required />
              </div>
              <button className="primary" type="submit">
                <Save size={17} /> Store review record
              </button>
            </form>
            <div className="timeline" style={{ marginTop: 14 }}>
              {(selected.score_reviews ?? []).map((review) => (
                <div className="timeline-item" key={review.id}>
                  <strong>{review.override_score}% by {review.reviewer}</strong>
                  <p className="muted small">{review.reason}</p>
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      {selected && tab === "transcript" && (
        <section className="panel" style={{ marginTop: 16 }}>
          <h2 className="section-title">Append-Only Transcript Events</h2>
          <div className="timeline">
            {selected.transcript_events.map((event) => (
              <div className={event.type === "proctoring_flag" ? "timeline-item flag" : "timeline-item"} key={event.id}>
                <strong>{event.sequence ? `#${event.sequence} ` : ""}{event.type}</strong>
                <div className="muted small">{new Date(event.created_at).toLocaleString()} · hash {event.event_hash?.slice(0, 12) ?? "legacy"}</div>
                <pre className="small">{JSON.stringify(event.payload, null, 2)}</pre>
              </div>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}
