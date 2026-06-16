"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  Camera,
  CheckCircle as CheckCircle2,
  CornersOut as Maximize2,
  Microphone as Mic,
  Monitor as MonitorUp,
  Pause,
  PauseCircle,
  Play,
  PaperPlaneTilt as Send,
  ShieldWarning as ShieldAlert,
  SpeakerHigh as Volume2,
  Warning as AlertTriangle,
} from "@phosphor-icons/react";
import { VivaAnswer, VivaQuestion, getMe, logout } from "../../lib/api";
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
import { useSessionRecorder } from "./hooks/useSessionRecorder";
import { useFaceLighting } from "./hooks/useFaceLighting";
import { useAiHealth } from "./hooks/useAiHealth";

const CONSENT_TEXT =
  "I consent to browser proctoring flags, camera video and audio recording, transcripts, " +
  "AI scoring, and professor review for this viva.";

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
  // Fullscreen is required to *start* a viva; exiting mid-viva logs a proctoring flag
  // (useProctoring) and drops permissions.fullscreen. We then show a blocking overlay
  // urging re-entry. The student may dismiss it (the no-trap invariant) and keep typing;
  // re-entering fullscreen re-arms the gate so a later exit re-prompts.
  const [fsDismissed, setFsDismissed] = useState(false);
  const handleFullscreenChange = useCallback(
    (active: boolean) => {
      setFullscreenActive(active);
      if (active) setFsDismissed(false);
    },
    [setFullscreenActive]
  );

  useProctoring({
    session: viva.session,
    videoRef,
    logEvent: viva.logEvent,
    onFullscreenChange: handleFullscreenChange,
  });
  // Records the camera (video+audio) for the whole viva for examiner review-only playback.
  const recorder = useSessionRecorder({
    session: viva.session,
    ensureCameraMic,
    logEvent: viva.logEvent,
  });
  // Lightweight check that the student's face is well lit in the camera preview, before
  // and during the viva (brightness sample, no heavy face/ML detection).
  const lighting = useFaceLighting(videoRef, mediaActive);
  // Heartbeat on the AI examiner: surfaces a banner when scoring degrades to the local
  // backup, and auto-clears when the provider reconnects.
  const ai = useAiHealth(viva.session?.status === "active");

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
  // Staff cannot sit a viva (the backend 403s start_session for a staff cookie). Detect
  // it up front so the entry form shows a clear "log out to take a viva" notice instead.
  const [staffSignedIn, setStaffSignedIn] = useState(false);
  const [switchingRole, setSwitchingRole] = useState(false);
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

  // Alt-tab / window-switch / minimize cannot be *prevented* (it is an OS action outside
  // the browser sandbox), but it can be enforced after the fact: when the student leaves
  // the exam window during an active viva we latch a flag and show a blocking overlay on
  // return that must be acknowledged. The proctoring flag is logged separately by
  // useProctoring. We latch on leave (not clear on return) so the overlay greets them back.
  const [leftWindow, setLeftWindow] = useState(false);
  const sessionActive = session?.status === "active";
  useEffect(() => {
    if (!sessionActive) return;
    const onLeave = () => setLeftWindow(true);
    const onVisibility = () => {
      if (document.hidden) setLeftWindow(true);
    };
    window.addEventListener("blur", onLeave);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("blur", onLeave);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [sessionActive]);

  // Keep permissions.fullscreen in sync with the *actual* fullscreen state at all times
  // — including before the viva starts. useProctoring only binds its fullscreenchange
  // listener during an active session, so without this a student could enter fullscreen,
  // press Esc, and still start (the flag stayed sticky-true). This makes canStart honest.
  useEffect(() => {
    const sync = () => setFullscreenActive(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, [setFullscreenActive]);

  // Probe identity once on mount: a staff cookie means the start form would 403.
  useEffect(() => {
    let cancelled = false;
    getMe()
      .then((me) => {
        if (!cancelled) setStaffSignedIn(me.role === "staff");
      })
      .catch(() => {
        if (!cancelled) setStaffSignedIn(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function switchToStudent() {
    setSwitchingRole(true);
    try {
      await logout();
    } catch {
      // Ignore — clearing the cookie is best-effort; reload reflects the new state.
    } finally {
      window.location.reload();
    }
  }

  async function startSession(event: FormEvent) {
    event.preventDefault();
    // Hard guard: never start unless the browser is actually in fullscreen right now.
    // Try to (re-)enter on the click gesture; bail if it still didn't take.
    if (!document.fullscreenElement) {
      await requestFullscreen();
      if (!document.fullscreenElement) return;
    }
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
  // Show the re-enter-fullscreen gate only during an active viva (not the start form,
  // not after completion) when fullscreen has been lost and not yet dismissed.
  const showFullscreenGate =
    session?.status === "active" && !permissions.fullscreen && !fsDismissed;
  // Show the "you left the exam" gate when latched during an active viva. Fullscreen
  // takes priority (re-entering fullscreen is the stronger requirement).
  const showFocusGate = sessionActive && leftWindow && !showFullscreenGate;

  return (
    <div>
      {showFullscreenGate && (
        <div
          role="alertdialog"
          aria-modal="true"
          aria-label="Return to fullscreen"
          className="fixed inset-0 z-50 flex items-center justify-center bg-ink/70 px-5 backdrop-blur-sm"
        >
          <Card className="w-full max-w-md p-7 text-center">
            <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full border border-warn/40 bg-warn-soft text-warn">
              <Maximize2 size={22} />
            </div>
            <h2 className="text-xl text-ink">Return to fullscreen</h2>
            <p className="mt-2 text-[0.9rem] leading-relaxed text-muted">
              Your viva must be taken in fullscreen. Leaving fullscreen is recorded as a
              proctoring flag for your examiner. Re-enter to continue.
            </p>
            <div className="mt-6 flex flex-col gap-2">
              <Button variant="primary" onClick={requestFullscreen} className="w-full">
                <Maximize2 size={16} /> Re-enter fullscreen
              </Button>
              <Button variant="ghost" onClick={() => setFsDismissed(true)} className="w-full">
                Continue without fullscreen
              </Button>
            </div>
          </Card>
        </div>
      )}

      {showFocusGate && (
        <div
          role="alertdialog"
          aria-modal="true"
          aria-label="You left the exam window"
          className="fixed inset-0 z-50 flex items-center justify-center bg-ink/70 px-5 backdrop-blur-sm"
        >
          <Card className="w-full max-w-md p-7 text-center">
            <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full border border-danger/40 bg-danger-soft text-danger">
              <ShieldAlert size={22} />
            </div>
            <h2 className="text-xl text-ink">You left the exam window</h2>
            <p className="mt-2 text-[0.9rem] leading-relaxed text-muted">
              Switching tabs or apps during the viva is recorded as a proctoring flag for
              your examiner. Stay on this window until you finish.
            </p>
            <Button variant="primary" onClick={() => setLeftWindow(false)} className="mt-6 w-full">
              Return to exam
            </Button>
          </Card>
        </div>
      )}

      <PageHeading
        eyebrow="№ 02 · Student Viva"
        title={restoring ? "Restoring your session…" : session ? "Your viva is in session." : "Begin your viva."}
      >
        Answer by voice or typing. The question stays on screen exactly as asked. Proctoring signals
        are logged as review flags only — they never affect your score.
      </PageHeading>

      {recorder.uploadFailed && (
        <Banner tone="warn" className="mb-4">
          <p className="font-medium">Your viva recording didn&apos;t upload.</p>
          <p className="mt-0.5">Stay on this page and retry so your examiner can review it.</p>
          <Button size="sm" variant="secondary" className="mt-2.5" onClick={() => recorder.retryUpload()}>
            Retry upload
          </Button>
        </Banner>
      )}

      {ai.degraded && (
        <Banner tone="warn" className="mb-4">
          <p className="flex items-center gap-2 font-medium">
            <span className="h-2 w-2 animate-pulse rounded-full bg-amber-500" aria-hidden />
            AI examiner running in backup mode
          </p>
          <p className="mt-0.5">
            The main AI is temporarily unavailable, so answers are scored by a local backup.
            Scoring may be slower and less detailed. Keep going as normal — your answers are saved,
            and we&apos;ll switch back to the full AI automatically the moment it reconnects.
          </p>
        </Banner>
      )}

      {ai.recovered && !ai.degraded && (
        <Banner tone="info" className="mb-4">
          <p className="font-medium">Full AI examiner reconnected — back to detailed scoring.</p>
        </Banner>
      )}

      <div className="grid gap-4 lg:grid-cols-[1.5fr_1fr]">
        {/* ── Main column ─────────────────────────────── */}
        <Card className="reveal p-6">
          {restoring && (
            <div className="flex flex-col items-center gap-3 py-12 text-center">
              <span className="h-9 w-9 animate-spin rounded-full border-2 border-line-strong border-t-accent" />
              <p className="text-[0.85rem] text-muted">Checking for an existing attempt…</p>
            </div>
          )}

          {!restoring && !session && staffSignedIn && (
            <Banner tone="warn" className="mb-6">
              <p className="font-medium">You&apos;re signed in as staff.</p>
              <p className="mt-0.5">
                Staff can&apos;t sit a viva. Log out to take one as a student, then sign back in
                afterward.
              </p>
              <Button
                size="sm"
                variant="secondary"
                className="mt-2.5"
                disabled={switchingRole}
                onClick={switchToStudent}
              >
                Log out &amp; take a viva
              </Button>
            </Banner>
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
                    <StatusPill tone={tts.speaking && !tts.paused ? "warn" : "neutral"} pulse={tts.speaking && !tts.paused}>
                      <Volume2 size={14} />{" "}
                      {tts.speaking ? (tts.paused ? "Paused" : "AI speaking") : "Question displayed"}
                    </StatusPill>
                    {/* Play / pause the question audio, or replay it once finished. */}
                    <button
                      type="button"
                      onClick={() =>
                        tts.speaking ? tts.togglePlayback() : tts.speakQuestion(currentQuestion)
                      }
                      aria-label={
                        tts.speaking ? (tts.paused ? "Resume question audio" : "Pause question audio") : "Replay question audio"
                      }
                      className="inline-flex h-7 items-center gap-1.5 rounded-full border border-line-strong bg-surface px-2.5 text-[0.72rem] font-medium text-ink-soft transition-colors hover:border-accent/40 hover:text-accent"
                    >
                      {tts.speaking ? (
                        tts.paused ? (
                          <>
                            <Play size={13} weight="fill" /> Resume
                          </>
                        ) : (
                          <>
                            <Pause size={13} weight="fill" /> Pause
                          </>
                        )
                      ) : (
                        <>
                          <Volume2 size={13} /> Replay
                        </>
                      )}
                    </button>
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
                    <p className="mt-1 text-[0.85rem] text-muted">
                      Review your answers if you like, then submit your viva for professor review.
                      Nothing is sent until you press the button below.
                    </p>
                  </div>
                  <Button variant="primary" onClick={viva.finalize} disabled={finalizing}>
                    <Send size={15} /> {finalizing ? "Submitting…" : "Submit viva for review"}
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
              {/* Mirror the self-view (like a webcam preview); CSS-only, so it does not
                  affect the raw frames the proctoring analysis reads via drawImage. */}
              <video ref={videoRef} autoPlay muted playsInline className="h-full w-full -scale-x-100 object-cover" />
            </div>
            {(() => {
              const copy = lightingCopy(lighting.status);
              if (!copy) return null;
              const toneCls =
                copy.tone === "ok"
                  ? "text-ok"
                  : copy.tone === "warn"
                    ? "text-warn"
                    : "text-muted";
              return (
                <div className="px-5 pb-5 pt-4">
                  <p className={`flex items-center gap-1.5 text-[0.78rem] font-medium ${toneCls}`}>
                    {copy.tone === "ok" ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}
                    {copy.label}
                  </p>
                  {lighting.warn && (
                    <p className="mt-1.5 text-[0.72rem] leading-snug text-muted">
                      Your face isn&apos;t well lit. This is recorded and flagged for your examiner,
                      who may adjust your marks. Improve your lighting.
                    </p>
                  )}
                </div>
              );
            })()}
          </Card>

          <Card className="reveal p-5" style={{ animationDelay: "140ms" }}>
            <SectionTitle marker="№ —" title="Proctoring flags" hint="Review only — never scored." />
            <div className="mt-4">
              {session?.proctoring_events.length ? (
                <Timeline>
                  {session.proctoring_events.slice(-8).reverse().map((event) => {
                    const copy = flagCopy(event.event_type);
                    return (
                      <TimelineItem key={event.id} tone={severityTone(event.severity)}>
                        <p className="text-[0.82rem] font-medium text-ink">{copy.label}</p>
                        <p className="text-[0.72rem] text-muted">{copy.detail}</p>
                        <p
                          className="text-[0.68rem] text-muted/80"
                          title={new Date(event.created_at).toLocaleString()}
                        >
                          {friendlyTime(event.created_at)}
                        </p>
                      </TimelineItem>
                    );
                  })}
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

/** Student-facing copy + tone for the live lighting check. null = nothing to show. */
function lightingCopy(
  status: import("./hooks/useFaceLighting").LightingStatus
): { label: string; tone: "ok" | "warn" | "muted" } | null {
  switch (status) {
    case "ok":
      return { label: "Lighting looks good", tone: "ok" };
    case "dark":
      return { label: "Too dark — add light on your face", tone: "warn" };
    case "bright":
      return { label: "Too bright — reduce the light behind you", tone: "warn" };
    case "checking":
      return { label: "Checking lighting…", tone: "muted" };
    default:
      return null;
  }
}

/** Human-readable label + plain-language explanation for each proctoring event type. */
const FLAG_COPY: Record<string, { label: string; detail: string }> = {
  window_blur: { label: "Left the exam window", detail: "Focus moved away from the exam." },
  tab_hidden: { label: "Switched tab or app", detail: "The exam tab was hidden." },
  fullscreen_exit: { label: "Exited fullscreen", detail: "The viva left fullscreen mode." },
  no_face: { label: "Face not clearly visible", detail: "The camera couldn’t see you clearly." },
  camera_blocked: { label: "Camera dark or blocked", detail: "The camera view was too dark." },
  multiple_faces: { label: "More than one face", detail: "Another person may be in view." },
  sustained_gaze_away: { label: "Looking away", detail: "Eyes were off the screen for a while." },
  media_unavailable: { label: "Camera or mic dropped", detail: "Recording devices became unavailable." },
};

function flagCopy(eventType: string): { label: string; detail: string } {
  return (
    FLAG_COPY[eventType] ?? {
      // Fallback: turn "some_event" into "Some event" so unknown types stay readable.
      label: eventType.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase()),
      detail: "Proctoring signal recorded for review.",
    }
  );
}

/** A relative, friendly time like "just now" / "2 min ago", with the clock time as a title. */
function friendlyTime(iso: string): string {
  const then = new Date(iso).getTime();
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 10) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  return new Date(iso).toLocaleTimeString();
}
