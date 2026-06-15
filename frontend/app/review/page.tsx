"use client";

import { FormEvent, useEffect, useState } from "react";
import { Warning as AlertTriangle, CheckCircle as CheckCircle2, ArrowsClockwise as RefreshCcw, FloppyDisk as Save, ShieldWarning as ShieldAlert } from "@phosphor-icons/react";
import { API_BASE, api, VivaSession } from "../../lib/api";
import { AuthPanel } from "../AuthPanel";
import { PageHeading } from "../../components/AppShell";
import { Card, SectionTitle } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Field, Input, Textarea } from "../../components/ui/Field";
import { Tabs } from "../../components/ui/Tabs";
import { Banner } from "../../components/ui/Banner";
import { StatusPill, ScorePill } from "../../components/ui/Pill";
import { Timeline, TimelineItem } from "../../components/ui/Timeline";

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

type Tab = "summary" | "answers" | "proctoring" | "transcript";

export default function ReviewPage() {
  const [sessions, setSessions] = useState<VivaSession[]>([]);
  const [selected, setSelected] = useState<ReviewSession | null>(null);
  const [message, setMessage] = useState("");
  const [success, setSuccess] = useState("");
  const [needsAuth, setNeedsAuth] = useState(false);
  const [tab, setTab] = useState<Tab>("summary");
  const [refreshing, setRefreshing] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  // Tracks in-flight per-action requests by key (e.g. "override", `rescore:<id>`)
  // so individual buttons can disable/spin without blocking unrelated controls.
  const [busy, setBusy] = useState<Record<string, boolean>>({});
  const isBusy = (key: string) => Boolean(busy[key]);

  function setBusyKey(key: string, value: boolean) {
    setBusy((prev) => ({ ...prev, [key]: value }));
  }

  async function loadSessions() {
    setRefreshing(true);
    try {
      const data = await api<VivaSession[]>("/api/review/sessions");
      setSessions(data);
      if (data[0]) {
        setDetailLoading(true);
        try {
          setSelected(await api<ReviewSession>(`/api/review/sessions/${data[0].id}`));
        } finally {
          setDetailLoading(false);
        }
      }
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    api("/api/auth/me").then(() => loadSessions()).catch(() => setNeedsAuth(true));
  }, []);

  async function selectSession(sessionId: string) {
    setMessage("");
    setSuccess("");
    setTab("summary");
    setDetailLoading(true);
    try {
      setSelected(await api<ReviewSession>(`/api/review/sessions/${sessionId}`));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Failed to load session.");
    } finally {
      setDetailLoading(false);
    }
  }

  async function refreshSelected() {
    if (!selected) return;
    setSelected(await api<ReviewSession>(`/api/review/sessions/${selected.id}`));
  }

  async function submitOverride(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || isBusy("override")) return;
    const formEl = event.currentTarget;
    const form = new FormData(formEl);
    setMessage("");
    setBusyKey("override", true);
    try {
      await api<ScoreReview>(`/api/review/sessions/${selected.id}/override`, {
        method: "POST",
        body: JSON.stringify({
          reviewer: form.get("reviewer"),
          override_score: Number(form.get("override_score")),
          reason: form.get("reason"),
        }),
      });
      formEl.reset();
      await refreshSelected();
      setSuccess("Override stored. The AI score is preserved.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Override failed.");
    } finally {
      setBusyKey("override", false);
    }
  }

  async function rescoreAnswer(answerId: string) {
    if (!selected) return;
    const key = `rescore:${answerId}`;
    if (isBusy(key)) return;
    setMessage("");
    setBusyKey(key, true);
    try {
      setSelected(
        await api<ReviewSession>(`/api/review/sessions/${selected.id}/answers/${answerId}/rescore`, {
          method: "POST",
        })
      );
      setSuccess("Answer re-scored.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Re-score failed.");
    } finally {
      setBusyKey(key, false);
    }
  }

  async function retranscribeAudio(audioId: string) {
    if (!selected) return;
    const key = `retranscribe:${audioId}`;
    if (isBusy(key)) return;
    setMessage("");
    setBusyKey(key, true);
    try {
      await api(`/api/review/sessions/${selected.id}/audio/${audioId}/retranscribe`, { method: "POST" });
      await refreshSelected();
      setSuccess("Audio re-transcribed.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Re-transcription failed.");
    } finally {
      setBusyKey(key, false);
    }
  }

  if (needsAuth) {
    return (
      <AuthPanel
        onReady={() => {
          setNeedsAuth(false);
          loadSessions().catch((error) => setMessage(error.message));
        }}
      />
    );
  }

  // Keep null when genuinely pending so ScorePill renders "—/pending" instead of "0%".
  const effective = selected ? selected.effective_score ?? selected.final_score ?? null : null;

  return (
    <div>
      <PageHeading
        eyebrow="№ 03 · Professor Review"
        title="Read the record. Record the verdict."
        action={
          <Button onClick={() => loadSessions()} disabled={refreshing} aria-busy={refreshing} aria-label="Refresh sessions">
            <RefreshCcw size={16} className={refreshing ? "animate-spin" : undefined} />
            {refreshing ? "Refreshing…" : "Refresh"}
          </Button>
        }
      >
        Review the transcript, per-answer reasoning, and proctoring timeline. An override stores a new
        record without disturbing the AI score.
      </PageHeading>

      <div className="grid gap-4 lg:grid-cols-[0.8fr_1.6fr]">
        {/* ── Session list ─────────────────────────── */}
        <Card className="reveal h-fit p-5">
          <SectionTitle marker="№ —" title="Sessions" />
          <div className="mt-4 flex flex-col gap-2">
            {sessions.map((session) => (
              <button
                key={session.id}
                type="button"
                onClick={() => selectSession(session.id)}
                className={`flex flex-col gap-1.5 rounded-[var(--radius-control)] border px-3.5 py-3 text-left transition-colors ${
                  selected?.id === session.id
                    ? "border-accent/50 bg-accent-soft"
                    : "border-line bg-surface-2/40 hover:border-accent/30"
                }`}
              >
                <span className="text-[0.88rem] font-medium text-ink">{session.student_name}</span>
                <span className="text-[0.74rem] text-muted">
                  <span className="font-mono">{session.roll_number}</span> · {session.status} ·{" "}
                  {session.effective_score ?? session.final_score ?? "pending"}%
                  {session.score_overridden ? " (override)" : ""}
                </span>
                <StatusPill tone={session.proctoring_count ? "warn" : "ok"}>
                  <ShieldAlert size={13} /> {session.proctoring_count ?? 0} flags
                </StatusPill>
              </button>
            ))}
            {!sessions.length && <p className="text-[0.82rem] text-muted">No viva sessions yet.</p>}
          </div>
          {message && (
            <div className="mt-4">
              <Banner tone="danger">{message}</Banner>
            </div>
          )}
          {success && (
            <div className="mt-4">
              <Banner tone="ok">{success}</Banner>
            </div>
          )}
        </Card>

        {/* ── Detail ─────────────────────────── */}
        {detailLoading && (
          <Card className="reveal flex h-fit items-center gap-3 p-6" aria-busy="true">
            <RefreshCcw size={16} className="animate-spin text-accent" />
            <span className="text-[0.86rem] text-muted">Loading session record…</span>
          </Card>
        )}

        {!detailLoading && selected && (
          <div className="flex flex-col gap-4">
            <Card className="reveal p-6">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-2xl text-ink">{selected.student_name}</h2>
                  <p className="mt-0.5 text-[0.82rem] text-muted">
                    {selected.exam_name} · <span className="font-mono">{selected.roll_number}</span>
                  </p>
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <StatusPill tone={selected.status === "completed" ? "ok" : "warn"}>
                      {selected.status === "completed" ? <CheckCircle2 size={13} /> : <AlertTriangle size={13} />}
                      {selected.status}
                    </StatusPill>
                    <StatusPill tone={selected.score_official ? "ok" : "warn"}>
                      {selected.score_status ?? "provisional"}
                      {selected.mark_mode === "ai_official" ? " · AI official" : ""}
                    </StatusPill>
                    <span className="text-[0.72rem] text-muted">
                      Started {new Date(selected.started_at).toLocaleString()}
                    </span>
                  </div>
                </div>
                <div className="text-center">
                  <ScorePill
                    value={effective}
                    tone={selected.score_overridden ? "accent" : "neutral"}
                    label={selected.score_overridden ? "override" : "AI score"}
                  />
                  {selected.score_overridden && (
                    <p className="mt-1 text-[0.68rem] text-muted">
                      AI {selected.final_score ?? 0}%
                      {selected.override_reviewer ? ` · ${selected.override_reviewer}` : ""}
                    </p>
                  )}
                </div>
              </div>

              <div className="mt-5">
                <Tabs
                  value={tab}
                  onValueChange={(value) => setTab(value as Tab)}
                  items={[
                    { value: "summary", label: "Summary" },
                    { value: "answers", label: "Answers" },
                    { value: "proctoring", label: "Proctoring" },
                    { value: "transcript", label: "Transcript" },
                  ]}
                />
              </div>
            </Card>

            {tab === "summary" && (
              <Card className="p-6">
                <SectionTitle marker="№ —" title="Summary" />
                <div className="mt-4 flex flex-wrap gap-2">
                  <StatusPill>Answers {selected.answers.length}/{selected.questions.length}</StatusPill>
                  <StatusPill tone={selected.proctoring_events.length ? "warn" : "ok"}>
                    Flags {selected.proctoring_events.length}
                  </StatusPill>
                  <StatusPill>{selected.status}</StatusPill>
                </div>
                {selected.rubric && (
                  <div className="mt-5">
                    <p className="mb-2 text-[0.72rem] uppercase tracking-[0.14em] text-muted">Rubric</p>
                    <pre className="whitespace-pre-wrap rounded-[var(--radius-control)] border border-line bg-surface-2/40 p-4 font-mono text-[0.78rem] leading-relaxed text-ink-soft">
                      {selected.rubric}
                    </pre>
                  </div>
                )}
              </Card>
            )}

            {tab === "answers" && (
              <Card className="p-6">
                <SectionTitle marker="№ —" title="Answers and scores" />
                <div className="mt-5 flex flex-col gap-3">
                  {selected.questions.map((question) => {
                    const answer = selected.answers.find((item) => item.question_id === question.id);
                    return (
                      <div key={question.id} className="rounded-[var(--radius-control)] border border-line bg-surface-2/40 p-4">
                        <div className="flex items-start justify-between gap-3">
                          <p className="font-display text-[1.02rem] text-ink">
                            Q{question.ordinal}. {question.text}
                          </p>
                          {answer && (
                            <span className="shrink-0 font-display text-lg text-accent tnum">
                              {answer.score}/{answer.max_score}
                            </span>
                          )}
                        </div>
                        <StatusPill tone="neutral" className="mt-2">{question.category}</StatusPill>

                        {answer ? (
                          <div className="mt-3">
                            <p className="text-[0.86rem] text-ink">{answer.answer_text}</p>
                            <div className="mt-2 flex flex-wrap items-center gap-2">
                              <StatusPill tone={answer.scoring_status === "pending_ai_error" ? "danger" : "ok"}>
                                {answer.scoring_status ?? "scored"} · {answer.scorer_provider ?? "unknown"}
                              </StatusPill>
                              {(() => {
                                const errored = answer.scoring_status === "pending_ai_error";
                                const key = `rescore:${answer.id}`;
                                const pending = isBusy(key);
                                return (
                                  <Button
                                    size="sm"
                                    variant={errored ? "danger" : "secondary"}
                                    disabled={pending}
                                    aria-busy={pending}
                                    aria-label={errored ? "Retry AI scoring for this answer" : "Re-score this answer"}
                                    onClick={() => rescoreAnswer(answer.id)}
                                  >
                                    <RefreshCcw size={13} className={pending ? "animate-spin" : undefined} />
                                    {pending ? "Re-scoring…" : errored ? "Retry AI scoring" : "Re-score"}
                                  </Button>
                                );
                              })()}
                            </div>
                            {answer.reasoning && (
                              <p className="mt-2 text-[0.8rem] italic text-muted">{answer.reasoning}</p>
                            )}
                            {answer.audio_ref && (
                              <audio
                                controls
                                aria-label={`Recorded answer audio for question ${question.ordinal}`}
                                src={`${API_BASE}/api/review/audio?ref=${encodeURIComponent(answer.audio_ref)}`}
                                className="mt-3 w-full"
                              />
                            )}
                            {answer.audio_ref &&
                              selected.audio_submissions
                                ?.filter((audio) => audio.audio_ref === answer.audio_ref)
                                .map((audio) => {
                                  const errored = audio.transcription_status === "pending_transcription_error";
                                  const key = `retranscribe:${audio.id}`;
                                  const pending = isBusy(key);
                                  return (
                                    <div key={audio.id} className="mt-2 flex flex-wrap items-center gap-2 text-[0.74rem] text-muted">
                                      <span>
                                        Transcript {audio.transcription_status} · {audio.transcription_provider}
                                        {audio.transcription_model ? ` · ${audio.transcription_model}` : ""}
                                      </span>
                                      <Button
                                        size="sm"
                                        variant={errored ? "danger" : "secondary"}
                                        disabled={pending}
                                        aria-busy={pending}
                                        aria-label={errored ? "Retry transcription for this audio" : "Re-transcribe this audio"}
                                        onClick={() => retranscribeAudio(audio.id)}
                                      >
                                        <RefreshCcw size={13} className={pending ? "animate-spin" : undefined} />
                                        {pending ? "Re-transcribing…" : errored ? "Retry transcription" : "Re-transcribe"}
                                      </Button>
                                    </div>
                                  );
                                })}
                          </div>
                        ) : (
                          <p className="mt-3 text-[0.82rem] text-muted">No answer recorded.</p>
                        )}
                      </div>
                    );
                  })}
                </div>
              </Card>
            )}

            {tab === "proctoring" && (
              <div className="grid gap-4 md:grid-cols-2">
                <Card className="p-6">
                  <SectionTitle marker="№ —" title="Proctoring timeline" hint="Review only — never scored." />
                  <div className="mt-5">
                    {selected.proctoring_events.length ? (
                      <Timeline>
                        {selected.proctoring_events.map((event) => (
                          <TimelineItem key={event.id} tone={event.severity === "high" ? "danger" : event.severity === "warning" ? "warn" : "neutral"}>
                            <p className="text-[0.84rem] font-medium text-ink">{event.event_type}</p>
                            <p className="text-[0.72rem] text-muted">
                              {new Date(event.created_at).toLocaleString()} · {event.severity ?? "warning"} ·
                              confidence {Math.round(event.confidence * 100)}%
                            </p>
                            <pre className="mt-1.5 overflow-x-auto font-mono text-[0.7rem] text-ink-soft">
                              {JSON.stringify(event.details, null, 2)}
                            </pre>
                          </TimelineItem>
                        ))}
                      </Timeline>
                    ) : (
                      <p className="text-[0.82rem] text-muted">No proctoring flags were logged.</p>
                    )}
                  </div>
                </Card>

                <Card className="h-fit p-6">
                  <SectionTitle marker="№ —" title="Score override" hint="Stored as a record; the AI score is preserved." />
                  <form onSubmit={submitOverride} className="mt-5">
                    <Field label="Reviewer" htmlFor="reviewer">
                      <Input id="reviewer" name="reviewer" required placeholder="Prof. name" />
                    </Field>
                    <Field label="Override score" htmlFor="override_score">
                      <Input id="override_score" name="override_score" type="number" min="0" max="100" step="0.1" required />
                    </Field>
                    <Field label="Reason" htmlFor="reason">
                      <Textarea id="reason" name="reason" required placeholder="Rationale for the override." />
                    </Field>
                    <Button
                      variant="primary"
                      type="submit"
                      className="w-full"
                      disabled={isBusy("override")}
                      aria-busy={isBusy("override")}
                    >
                      {isBusy("override") ? (
                        <>
                          <RefreshCcw size={15} className="animate-spin" /> Storing…
                        </>
                      ) : (
                        <>
                          <Save size={15} /> Store review record
                        </>
                      )}
                    </Button>
                  </form>
                  {(() => {
                    const reviews = selected.score_reviews ?? [];
                    if (!reviews.length) return null;
                    // Only the most recent override is live/effective; the rest are history.
                    const effectiveId = reviews.reduce((latest, r) =>
                      new Date(r.created_at).getTime() >= new Date(latest.created_at).getTime() ? r : latest
                    ).id;
                    return (
                      <div className="mt-5">
                        <Timeline>
                          {reviews.map((review) => {
                            const isEffective = review.id === effectiveId;
                            return (
                              <TimelineItem key={review.id} tone={isEffective ? "accent" : "neutral"}>
                                <div className="flex flex-wrap items-center gap-2">
                                  <p className="text-[0.84rem] font-medium text-ink">
                                    {review.override_score}% by {review.reviewer}
                                  </p>
                                  {isEffective ? (
                                    <StatusPill tone="accent">
                                      <CheckCircle2 size={12} /> Effective
                                    </StatusPill>
                                  ) : (
                                    <StatusPill tone="neutral">Superseded</StatusPill>
                                  )}
                                </div>
                                <p className="text-[0.78rem] text-muted">{review.reason}</p>
                              </TimelineItem>
                            );
                          })}
                        </Timeline>
                      </div>
                    );
                  })()}
                </Card>
              </div>
            )}

            {tab === "transcript" && (
              <Card className="p-6">
                <SectionTitle marker="№ —" title="Append-only transcript" hint="Each event hash-chained to the previous." />
                <div className="mt-5">
                  <Timeline>
                    {selected.transcript_events.map((event) => (
                      <TimelineItem key={event.id} tone={event.type === "proctoring_flag" ? "warn" : "neutral"}>
                        <p className="text-[0.82rem] font-medium text-ink">
                          {event.sequence ? `#${event.sequence} ` : ""}
                          {event.type}
                        </p>
                        <p className="text-[0.7rem] text-muted">
                          {new Date(event.created_at).toLocaleString()} · hash{" "}
                          <span className="font-mono">{event.event_hash?.slice(0, 12) ?? "legacy"}</span>
                        </p>
                        <pre className="mt-1.5 overflow-x-auto font-mono text-[0.7rem] text-ink-soft">
                          {JSON.stringify(event.payload, null, 2)}
                        </pre>
                      </TimelineItem>
                    ))}
                  </Timeline>
                </div>
              </Card>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
