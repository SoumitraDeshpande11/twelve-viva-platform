"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import {
  Camera,
  CheckCircle as CheckCircle2,
  CornersOut as Maximize2,
  Microphone as Mic,
  Monitor as MonitorUp,
  PauseCircle,
  PaperPlaneTilt as Send,
  ShieldWarning as ShieldAlert,
  SpeakerHigh as Volume2,
  Warning as AlertTriangle,
} from "@phosphor-icons/react";
import { VivaAnswer, VivaQuestion } from "../../lib/api";
import { PageHeading } from "../../components/AppShell";
import { Card, SectionTitle } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Field, Input, Textarea } from "../../components/ui/Field";
import { Select } from "../../components/ui/Select";
import { Banner } from "../../components/ui/Banner";
import { StatusPill, ScorePill } from "../../components/ui/Pill";
import { Timeline, TimelineItem } from "../../components/ui/Timeline";
import { useVivaSession } from "./hooks/useVivaSession";
import { useMediaCapture } from "./hooks/useMediaCapture";
import { useProctoring } from "./hooks/useProctoring";
import { useQuestionTts } from "./hooks/useQuestionTts";
import { useVoiceRecorder } from "./hooks/useVoiceRecorder";

const CONSENT_TEXT =
  "I consent to browser proctoring flags, audio recording, transcripts, AI scoring, " +
  "and professor review for this viva.";

export default function StudentPage() {
  const viva = useVivaSession();
  const media = useMediaCapture(viva.logEvent, viva.setStatus);
  // Destructure media members up front so the (ref-bearing) `media` object is not
  // read during JSX render — this keeps the React-Compiler ref advisory quiet.
  const {
    mediaActive,
    mediaError,
    permissions,
    videoRef,
    ensureCameraMic,
    requestCameraMic,
    requestFullscreen,
    requestScreen,
    setFullscreenActive,
  } = media;
  const tts = useQuestionTts(viva.session, viva.logLiveTurnEvent);
  const voice = useVoiceRecorder({
    session: viva.session,
    ensureCameraMic,
    logLiveTurnEvent: viva.logLiveTurnEvent,
    notify: viva.setStatus,
  });
  useProctoring({
    session: viva.session,
    videoRef,
    logEvent: viva.logEvent,
    onFullscreenChange: setFullscreenActive,
  });

  // Destructure hook returns so member reads happen once here rather than during
  // JSX render — keeps the React-Compiler ref advisory quiet and the JSX terse.
  const { session, status, submitting, finalizing, restoring } = viva;
  const {
    recording,
    liveTranscript,
    setLiveTranscript,
    hasUploadedAudio,
    uploadFailed,
    serverTranscriptStatus,
  } = voice;

  const [examId, setExamId] = useState("");
  const [rollNumber, setRollNumber] = useState("");
  const [oneTimeCode, setOneTimeCode] = useState("");
  const [consent, setConsent] = useState(false);
  const [answer, setAnswer] = useState("");
  const lastQuestionId = useRef<string | null>(null);
  // Dedupe ref (not state) so logging a media-loss flag never re-renders.
  const mediaLossFlagged = useRef(false);

  // Default the exam selection to the first exam without an effect (avoids a
  // synchronous setState-in-effect render cascade). User selection wins once made.
  const selectedExamId = examId || viva.exams[0]?.id || "";

  // On a new question: log, speak, and clear the previous answer inputs.
  useEffect(() => {
    const current = session?.current_question;
    if (!current || lastQuestionId.current === current.id) return;
    lastQuestionId.current = current.id;
    setAnswer("");
    voice.resetForQuestion();
    void viva.logLiveTurnEvent("question_displayed", current.id, { ordinal: current.ordinal });
    void tts.speakQuestion(current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.current_question?.id]);

  // When media is lost mid-viva and can't be reacquired, log one review flag so the
  // exam can still proceed (typed fallback) without trapping the student.
  useEffect(() => {
    if (session?.status === "active" && !mediaActive && !mediaLossFlagged.current) {
      mediaLossFlagged.current = true;
      void viva.logEvent("media_unavailable", { recoverable: false }, 1, undefined, "high");
    }
    if (mediaActive) mediaLossFlagged.current = false;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session?.status, mediaActive]);

  async function startSession(event: FormEvent) {
    event.preventDefault();
    await viva.startSession(selectedExamId, rollNumber, oneTimeCode, permissions, CONSENT_TEXT);
  }

  async function submit(mode: "voice" | "typed") {
    const text = mode === "voice" ? liveTranscript || answer : answer;
    const ok = await viva.submitAnswer(mode, text, mode === "voice" ? voice.audioId : null);
    if (ok) {
      setAnswer("");
      voice.resetForQuestion();
    }
  }

  const currentQuestion: VivaQuestion | null = session?.current_question ?? null;
  const formReady = Boolean(selectedExamId && rollNumber && oneTimeCode && consent);
  const canStart = mediaActive && permissions.fullscreen && formReady;
  const completed = session?.status === "completed";
  // Media being unavailable must never trap the student: typed answers and finalize
  // stay available (a review flag is logged), voice paths still prefer reacquisition.
  const typedSubmittable = Boolean(answer.trim()) && !submitting;
  const voiceSubmittable = hasUploadedAudio && !submitting;

  return (
    <div>
      <PageHeading
        eyebrow="№ 02 · Student Viva"
        title={restoring ? "Restoring your session…" : session ? "Your viva is in session." : "Begin your viva."}
      >
        Answer by voice or typing. The question stays on screen exactly as asked. Proctoring signals
        are logged as review flags only — they never affect your score.
      </PageHeading>

      <div className="grid gap-4 lg:grid-cols-[1.5fr_1fr]">
        {/* ── Main column ─────────────────────────────── */}
        <Card className="reveal p-6">
          {restoring && (
            <div className="flex flex-col items-center gap-3 py-12 text-center">
              <span className="h-9 w-9 animate-spin rounded-full border-2 border-line-strong border-t-accent" />
              <p className="text-[0.85rem] text-muted">Checking for an existing attempt…</p>
            </div>
          )}

          {!restoring && !session && (
            <form onSubmit={startSession}>
              <SectionTitle marker="№ 01" title="Session Start" hint="Grant camera, mic and fullscreen, then enter your code." />
              <div className="mt-6">
                <Field label="Exam" htmlFor="exam">
                  <Select
                    id="exam"
                    value={selectedExamId}
                    onValueChange={setExamId}
                    placeholder="Choose your exam"
                    options={viva.exams.map((exam) => ({ value: exam.id, label: exam.name }))}
                  />
                </Field>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="Roll number" htmlFor="roll">
                    <Input id="roll" value={rollNumber} onChange={(e) => setRollNumber(e.target.value)} required />
                  </Field>
                  <Field label="One-time code" htmlFor="code">
                    <Input id="code" value={oneTimeCode} onChange={(e) => setOneTimeCode(e.target.value)} required />
                  </Field>
                </div>

                <div className="my-2 flex flex-wrap gap-2">
                  <Button variant={permissions.camera ? "ok" : "secondary"} onClick={requestCameraMic}>
                    <Camera size={16} /> Camera + mic
                  </Button>
                  <Button variant={permissions.fullscreen ? "ok" : "secondary"} onClick={requestFullscreen}>
                    <Maximize2 size={16} /> Fullscreen
                  </Button>
                  <Button variant={permissions.screen ? "ok" : "secondary"} onClick={requestScreen}>
                    <MonitorUp size={16} /> Screen share
                  </Button>
                </div>

                <div className="mb-4 flex flex-wrap gap-2">
                  <PermBadge ok={permissions.camera}>Camera</PermBadge>
                  <PermBadge ok={permissions.microphone}>Mic</PermBadge>
                  <PermBadge ok={permissions.fullscreen}>Fullscreen</PermBadge>
                  <PermBadge ok={permissions.screen} optional>Screen</PermBadge>
                </div>

                {/* Blocking, actionable guidance when camera/mic can't be opened. */}
                {mediaError && (
                  <div className="mb-4">
                    <Banner tone="danger">
                      <div className="flex flex-col gap-2">
                        <span className="font-medium">{mediaError.message}</span>
                        <div>
                          <Button size="sm" variant="secondary" onClick={requestCameraMic}>
                            <Camera size={14} /> Retry camera &amp; mic
                          </Button>
                        </div>
                      </div>
                    </Banner>
                  </div>
                )}

                <label className="mb-5 flex cursor-pointer items-start gap-2.5 rounded-[var(--radius-control)] border border-line bg-surface-2/40 px-3.5 py-3 text-[0.82rem] leading-snug text-ink-soft">
                  <input
                    type="checkbox"
                    checked={consent}
                    onChange={(e) => setConsent(e.target.checked)}
                    className="mt-0.5 h-4 w-4 accent-[var(--accent)]"
                  />
                  <span>{CONSENT_TEXT}</span>
                </label>

                {/* Per-requirement status so the disabled Start button is never opaque. */}
                <div className="mb-3 flex flex-wrap gap-x-4 gap-y-1 text-[0.78rem]">
                  <ReqStatus ok={mediaActive} label="Camera & mic" onFix={requestCameraMic} fixLabel="grant access" />
                  <ReqStatus ok={permissions.fullscreen} label="Fullscreen" onFix={requestFullscreen} fixLabel="click to enter" />
                  <ReqStatus ok={formReady} label="Code, roll & consent" />
                </div>

                <Button variant="primary" type="submit" disabled={!canStart} className="w-full">
                  Start viva
                </Button>
              </div>
            </form>
          )}

          {!restoring && session && (
            <div>
              <div className="mb-5 flex items-center justify-between">
                <StatusPill tone={completed ? "ok" : "warn"}>
                  {completed ? <CheckCircle2 size={14} /> : <PauseCircle size={14} />}
                  {session.status}
                </StatusPill>
                <ScoreDisplay session={session} />
              </div>

              {/* Prominent blocking banner on mid-viva media loss, with Retry. */}
              {!mediaActive && session.status === "active" && (
                <div className="mb-4">
                  <Banner tone="danger">
                    <div className="flex flex-col gap-2">
                      <span className="font-medium">
                        {mediaError?.message ??
                          "Camera and mic are no longer active. Reacquire to keep proctoring on."}
                      </span>
                      <span className="text-[0.78rem]">
                        You can still submit a typed answer and finish the viva — the interruption is
                        logged as a review flag, never scored.
                      </span>
                      <div>
                        <Button size="sm" variant="secondary" onClick={requestCameraMic}>
                          <Camera size={14} /> Retry camera &amp; mic
                        </Button>
                      </div>
                    </div>
                  </Banner>
                </div>
              )}

              {currentQuestion ? (
                <>
                  <div className="mb-3 flex items-center gap-2">
                    <StatusPill tone="accent">{currentQuestion.category}</StatusPill>
                    <StatusPill tone={tts.speaking ? "warn" : "neutral"} pulse={tts.speaking}>
                      <Volume2 size={14} /> {tts.speaking ? "AI speaking" : "Question displayed"}
                    </StatusPill>
                    <span className="ml-auto font-mono text-[0.72rem] text-muted">
                      Q{currentQuestion.ordinal}
                    </span>
                  </div>

                  <p className="font-display text-[1.35rem] leading-snug text-ink">
                    {currentQuestion.text}
                  </p>

                  <div className="mt-6 grid gap-4">
                    <Field label="Typed answer" htmlFor="answer">
                      <Textarea id="answer" value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder="Type your answer, or record it by voice below." />
                    </Field>
                    <Field label="Voice transcript" htmlFor="transcript" hint="Editable. The server transcript of your recording is authoritative for scoring.">
                      <Textarea
                        id="transcript"
                        value={liveTranscript}
                        onChange={(e) => setLiveTranscript(e.target.value)}
                        placeholder="Your spoken answer appears here after recording. If voice is unavailable, type your answer above."
                      />
                    </Field>
                  </div>

                  <div className="mt-4 flex flex-wrap items-center gap-2">
                    <Button variant={recording ? "danger" : "secondary"} onClick={voice.toggleVoice}>
                      <Mic size={16} /> {recording ? "Stop voice" : "Record voice"}
                    </Button>
                    {hasUploadedAudio && <StatusPill tone="ok">Audio stored</StatusPill>}
                    {uploadFailed && (
                      <Button size="sm" variant="danger" onClick={voice.retryUpload}>
                        <Send size={14} /> Upload failed — Retry
                      </Button>
                    )}
                    <div className="ml-auto flex gap-2">
                      <Button
                        variant="secondary"
                        onClick={() => submit("voice")}
                        disabled={!voiceSubmittable}
                      >
                        <Send size={15} /> Submit voice
                      </Button>
                      <Button
                        variant="primary"
                        onClick={() => submit("typed")}
                        disabled={!typedSubmittable}
                      >
                        <Send size={15} /> {submitting ? "Submitting…" : "Submit typed"}
                      </Button>
                    </div>
                  </div>

                  {serverTranscriptStatus && (
                    <p className="mt-3 text-[0.78rem] text-muted">{serverTranscriptStatus}</p>
                  )}
                  {!mediaActive && (
                    <p className="mt-2 text-[0.78rem] text-muted">
                      Voice needs camera &amp; mic. Type your answer above and use Submit typed.
                    </p>
                  )}
                </>
              ) : completed ? (
                <div className="flex flex-col items-center gap-4 py-8 text-center">
                  <span className="flex h-14 w-14 items-center justify-center rounded-full border border-ok/30 bg-ok-soft text-ok">
                    <CheckCircle2 size={26} />
                  </span>
                  <div>
                    <h2 className="text-xl text-ink">Submitted for review</h2>
                    <p className="mt-1 text-[0.85rem] text-muted">
                      Your viva has been finalized and sent to your professor.
                    </p>
                  </div>
                </div>
              ) : (
                <div className="flex flex-col items-center gap-4 py-8 text-center">
                  <span className="flex h-14 w-14 items-center justify-center rounded-full border border-ok/30 bg-ok-soft text-ok">
                    <CheckCircle2 size={26} />
                  </span>
                  <div>
                    <h2 className="text-xl text-ink">All questions answered</h2>
                    <p className="mt-1 text-[0.85rem] text-muted">Finalize to send your viva for professor review.</p>
                  </div>
                  <Button variant="primary" onClick={viva.finalize} disabled={finalizing}>
                    {finalizing ? "Finalizing…" : "Finalize score"}
                  </Button>
                </div>
              )}

              {status && (
                <div className="mt-4">
                  <Banner tone="info">{status}</Banner>
                </div>
              )}
            </div>
          )}
        </Card>

        {/* ── Sidebar ─────────────────────────────── */}
        <div className="flex flex-col gap-4">
          <Card className="reveal overflow-hidden p-0" style={{ animationDelay: "80ms" }}>
            <div className="flex items-center justify-between px-5 pt-5">
              <SectionTitle marker="№ —" title="Camera" />
              {mediaError && !session && <span className="text-[0.7rem] text-danger">offline</span>}
            </div>
            <div className="mt-4 aspect-video w-full bg-ink">
              <video ref={videoRef} autoPlay muted playsInline className="h-full w-full object-cover" />
            </div>
          </Card>

          <Card className="reveal p-5" style={{ animationDelay: "140ms" }}>
            <SectionTitle marker="№ —" title="Proctoring flags" hint="Review only — never scored." />
            <div className="mt-4">
              {session?.proctoring_events.length ? (
                <Timeline>
                  {session.proctoring_events.slice(-8).reverse().map((event) => (
                    <TimelineItem key={event.id} tone={severityTone(event.severity)}>
                      <p className="text-[0.82rem] font-medium text-ink">{event.event_type}</p>
                      <p className="text-[0.72rem] text-muted">
                        {new Date(event.created_at).toLocaleTimeString()} · confidence{" "}
                        {Math.round(event.confidence * 100)}%
                      </p>
                    </TimelineItem>
                  ))}
                </Timeline>
              ) : (
                <p className="flex items-center gap-2 text-[0.82rem] text-muted">
                  <ShieldAlert size={15} /> No flags logged.
                </p>
              )}
            </div>
          </Card>
        </div>
      </div>

      {session && session.answers.length > 0 && (
        <Card className="reveal mt-4 p-6">
          <SectionTitle marker="№ 02" title="Transcript preview" />
          <div className="mt-5 flex flex-col gap-3">
            {session.answers.map((item) => (
              <AnswerRow key={item.id} item={item} />
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

/** Shows the score with provisional/pending context instead of a bare percentage. */
function ScoreDisplay({ session }: { session: { final_score: number | null; effective_score?: number | null; score_status?: string; mark_mode?: string; status: string } }) {
  const score = session.effective_score ?? session.final_score;
  if (score === null || score === undefined) return null;
  const provisional = session.score_status === "provisional" || session.mark_mode === "professor_approved";
  return (
    <div className="flex flex-col items-end">
      <span className="font-display text-2xl text-accent tnum">{score}%</span>
      {provisional && (
        <span className="text-[0.7rem] text-warn">Provisional — pending professor review</span>
      )}
    </div>
  );
}

function AnswerRow({ item }: { item: VivaAnswer }) {
  const pending = item.scoring_status === "pending_ai_error" || item.scoring_status === "pending";
  return (
    <div className="flex gap-4 rounded-[var(--radius-control)] border border-line bg-surface-2/40 p-4">
      <ScorePill value={pending ? null : item.score} tone={pending ? "neutral" : "accent"} />
      <div className="min-w-0 flex-1">
        <p className="text-[0.82rem] text-muted">
          {pending ? "Scoring pending — a professor will review this answer." : item.reasoning}
        </p>
        <p className="mt-1.5 text-[0.85rem] text-ink">{item.answer_text}</p>
      </div>
    </div>
  );
}

function ReqStatus({ ok, label, onFix, fixLabel }: { ok: boolean; label: string; onFix?: () => void; fixLabel?: string }) {
  if (ok) {
    return (
      <span className="flex items-center gap-1.5 text-ok">
        <CheckCircle2 size={14} weight="fill" /> {label}
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1.5 text-muted">
      <AlertTriangle size={14} /> {label}
      {onFix && fixLabel && (
        <button type="button" onClick={onFix} className="text-accent underline underline-offset-2">
          {fixLabel}
        </button>
      )}
    </span>
  );
}

function PermBadge({ ok, optional, children }: { ok: boolean; optional?: boolean; children: React.ReactNode }) {
  return (
    <StatusPill tone={ok ? "ok" : optional ? "neutral" : "danger"}>
      {children}
      {optional && !ok ? " · optional" : ""}
    </StatusPill>
  );
}

function severityTone(severity?: string): "warn" | "danger" | "neutral" {
  if (severity === "high") return "danger";
  if (severity === "warning") return "warn";
  return "neutral";
}
