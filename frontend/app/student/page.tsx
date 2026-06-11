"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { Camera, CheckCircle2, Maximize2, Mic, MonitorUp, PauseCircle, Send, Volume2 } from "lucide-react";
import { API_BASE, api, Exam, makeIdempotencyKey, ProctoringEvent, VivaQuestion, VivaSession } from "../../lib/api";

type PermissionState = {
  camera: boolean;
  microphone: boolean;
  fullscreen: boolean;
  screen: boolean;
};

type SpeechRecognitionLike = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start: () => void;
  stop: () => void;
  onresult: ((event: { results: ArrayLike<{ 0: { transcript: string }; isFinal: boolean }> }) => void) | null;
  onend: (() => void) | null;
};

declare global {
  interface Window {
    webkitSpeechRecognition?: new () => SpeechRecognitionLike;
    SpeechRecognition?: new () => SpeechRecognitionLike;
    FaceDetector?: new (options?: { fastMode?: boolean; maxDetectedFaces?: number }) => {
      detect: (image: HTMLVideoElement) => Promise<Array<{ boundingBox: DOMRectReadOnly }>>;
    };
  }
}

export default function StudentPage() {
  const [exams, setExams] = useState<Exam[]>([]);
  const [examId, setExamId] = useState("");
  const [rollNumber, setRollNumber] = useState("");
  const [oneTimeCode, setOneTimeCode] = useState("");
  const [consent, setConsent] = useState(false);
  const [permissions, setPermissions] = useState<PermissionState>({ camera: false, microphone: false, fullscreen: false, screen: false });
  const [mediaActive, setMediaActive] = useState(false);
  const [session, setSession] = useState<VivaSession | null>(null);
  const [answer, setAnswer] = useState("");
  const [liveTranscript, setLiveTranscript] = useState("");
  const [pendingAudioRef, setPendingAudioRef] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [status, setStatus] = useState("");
  const [serverTranscriptStatus, setServerTranscriptStatus] = useState("");
  const [speaking, setSpeaking] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const cameraStreamRef = useRef<MediaStream | null>(null);
  const screenStreamRef = useRef<MediaStream | null>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<BlobPart[]>([]);
  const questionAudioRef = useRef<HTMLAudioElement | null>(null);
  const lastQuestionId = useRef<string | null>(null);
  const liveTranscriptRef = useRef("");
  const lastFlagRef = useRef<Record<string, number>>({});
  const gazeMisses = useRef(0);

  useEffect(() => {
    api<Exam[]>("/api/public/exams").then((data) => {
      setExams(data);
      setExamId(data[0]?.id ?? "");
    }).catch((error) => setStatus(error.message));
    api<VivaSession>("/api/student/attempts/current").then(setSession).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!session?.current_question || lastQuestionId.current === session.current_question.id) {
      return;
    }
    lastQuestionId.current = session.current_question.id;
    void logLiveTurnEvent("question_displayed", session.current_question.id, { ordinal: session.current_question.ordinal });
    speakQuestion(session.current_question);
  }, [session?.current_question]);

  useEffect(() => {
    liveTranscriptRef.current = liveTranscript;
  }, [liveTranscript]);

  useEffect(() => {
    if (!session) {
      return;
    }
    const onVisibility = () => {
      if (document.hidden) {
        logEvent("tab_hidden", { state: "hidden" }, 1, undefined, "high");
      }
    };
    const onBlur = () => logEvent("window_blur", { state: "blurred" }, 0.9, undefined, "warning");
    const onFullscreen = () => {
      const active = Boolean(document.fullscreenElement);
      setPermissions((current) => ({ ...current, fullscreen: active }));
      if (!active) {
        logEvent("fullscreen_exit", {}, 1, undefined, "high");
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    document.addEventListener("fullscreenchange", onFullscreen);
    window.addEventListener("blur", onBlur);
    const interval = window.setInterval(analyzeCamera, 3500);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      document.removeEventListener("fullscreenchange", onFullscreen);
      window.removeEventListener("blur", onBlur);
      window.clearInterval(interval);
    };
  }, [session]);

  async function requestCameraMic() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
      cameraStreamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
      }
      stream.getAudioTracks().forEach((track) => {
        track.onmute = () => logEvent("mic_muted", { label: track.label }, 1);
        track.onended = () => {
          setMediaActive(false);
          logEvent("mic_lost", { ended: true, label: track.label }, 1, undefined, "high");
        };
      });
      stream.getVideoTracks().forEach((track) => {
        track.onended = () => {
          setMediaActive(false);
          logEvent("camera_lost", { label: track.label }, 1, undefined, "high");
        };
      });
      setMediaActive(true);
      setPermissions((current) => ({ ...current, camera: true, microphone: true }));
      setStatus("");
    } catch (error) {
      setMediaActive(false);
      setPermissions((current) => ({ ...current, camera: false, microphone: false }));
      setStatus(error instanceof Error ? error.message : "Camera and microphone could not be opened.");
    }
  }

  async function requestScreen() {
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
      screenStreamRef.current = stream;
      stream.getVideoTracks()[0].onended = () => {
        setPermissions((current) => ({ ...current, screen: false }));
        logEvent("screen_share_stopped", {}, 1, undefined, "high");
      };
      setPermissions((current) => ({ ...current, screen: true }));
      setStatus("");
    } catch (error) {
      setPermissions((current) => ({ ...current, screen: false }));
      setStatus(error instanceof Error ? error.message : "Screen share could not be started.");
    }
  }

  async function requestFullscreen() {
    try {
      await document.documentElement.requestFullscreen();
      setPermissions((current) => ({ ...current, fullscreen: true }));
      setStatus("");
    } catch (error) {
      setPermissions((current) => ({ ...current, fullscreen: false }));
      setStatus(error instanceof Error ? error.message : "Fullscreen could not be started.");
    }
  }

  async function startSession(event: FormEvent) {
    event.preventDefault();
    setStatus("");
    setServerTranscriptStatus("");
    try {
      const data = await api<VivaSession>("/api/student/attempts/start", {
        method: "POST",
        body: JSON.stringify({ exam_id: examId, roll_number: rollNumber, one_time_code: oneTimeCode, permissions })
      });
      setSession(data);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Unable to start session.");
    }
  }

  async function submitAnswer(mode: "voice" | "typed") {
    if (!session?.current_question) {
      return;
    }
    setSubmitting(true);
    setStatus("");
    const answerText = (mode === "voice" ? liveTranscript || answer : answer).trim();
    if (!answerText) {
      setStatus("Answer cannot be empty.");
      setSubmitting(false);
      return;
    }
    try {
      await logLiveTurnEvent("answer_submit_started", session.current_question.id, { input_mode: mode });
      const updated = await api<VivaSession>("/api/student/attempts/current/answers", {
        method: "POST",
        headers: { "Idempotency-Key": makeIdempotencyKey() },
        body: JSON.stringify({ question_id: session.current_question.id, answer_text: answerText, input_mode: mode, audio_ref: mode === "voice" ? pendingAudioRef : null })
      });
      await logLiveTurnEvent("answer_submit_completed", session.current_question.id, { input_mode: mode });
      setSession(updated);
      setAnswer("");
      setLiveTranscript("");
      setPendingAudioRef(null);
      setServerTranscriptStatus("");
    } catch (error) {
      await logLiveTurnEvent("answer_submit_failed", session.current_question.id, { input_mode: mode });
      setStatus(error instanceof Error ? error.message : "Answer could not be submitted.");
    } finally {
      setSubmitting(false);
    }
  }

  async function finalize() {
    if (!session) {
      return;
    }
    setSession(await api<VivaSession>(`/api/sessions/${session.id}/finalize`, { method: "POST" }));
  }

  async function logEvent(event_type: string, details: Record<string, unknown>, confidence: number, duration_ms?: number, severity: "info" | "warning" | "high" = "warning") {
    if (!session) {
      return;
    }
    const key = `${event_type}:${session.current_question?.id ?? "none"}`;
    const now = Date.now();
    if ((lastFlagRef.current[key] ?? 0) > now - 2500) {
      return;
    }
    lastFlagRef.current[key] = now;
    try {
      const event = await api<ProctoringEvent>("/api/student/attempts/current/proctoring-events", {
        method: "POST",
        body: JSON.stringify({ event_type, details, confidence, duration_ms, severity })
      });
      setSession((current) => current ? { ...current, proctoring_events: [...current.proctoring_events, event] } : current);
    } catch {
      setStatus("Proctoring event could not be logged; network may be unavailable.");
    }
  }

  async function logLiveTurnEvent(event_type: string, question_id?: string, payload: Record<string, unknown> = {}) {
    if (!session) {
      return;
    }
    try {
      await api("/api/student/attempts/current/live-turn-events", {
        method: "POST",
        body: JSON.stringify({ event_type, question_id, payload })
      });
    } catch {
      // Live turn events are audit helpers; answer/proctoring flows handle their own failures.
    }
  }

  async function speakQuestion(question: VivaQuestion) {
    setSpeaking(true);
    window.speechSynthesis?.cancel();
    questionAudioRef.current?.pause();
    questionAudioRef.current = null;

    if (session) {
      try {
        await logLiveTurnEvent("tts_started", question.id, { provider: "server-or-browser" });
        const response = await fetch(`${API_BASE}/api/sessions/${session.id}/questions/${question.id}/tts`, { cache: "no-store", credentials: "include" });
        if (response.ok) {
          const blob = await response.blob();
          const audio = new Audio(URL.createObjectURL(blob));
          questionAudioRef.current = audio;
          audio.onended = () => {
            setSpeaking(false);
            void logLiveTurnEvent("tts_completed", question.id, { provider: "gemini" });
          };
          audio.onerror = () => {
            setSpeaking(false);
            speakQuestionWithBrowser(question.text);
          };
          await audio.play();
          return;
        }
      } catch {
        // Fall through to browser speech synthesis.
      }
    }
    speakQuestionWithBrowser(question.text);
  }

  function speakQuestionWithBrowser(text: string) {
    if (!("speechSynthesis" in window)) {
      setSpeaking(false);
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 0.92;
    utterance.onstart = () => setSpeaking(true);
    utterance.onend = () => {
      setSpeaking(false);
      if (session?.current_question) {
        void logLiveTurnEvent("tts_completed", session.current_question.id, { provider: "browser" });
      }
    };
    window.speechSynthesis.speak(utterance);
  }

  async function toggleVoice() {
    if (recording) {
      recognitionRef.current?.stop();
      mediaRecorderRef.current?.stop();
      setRecording(false);
      return;
    }
    if (!cameraStreamRef.current) {
      await requestCameraMic();
      if (!cameraStreamRef.current) {
        setStatus("Grant camera and mic before recording.");
        return;
      }
    }
    if (!("MediaRecorder" in window)) {
      setStatus("Media recording is not available in this browser. Use typed answer fallback.");
      return;
    }
    setServerTranscriptStatus("");
    setPendingAudioRef(null);
    audioChunksRef.current = [];
    const audioStream = new MediaStream(cameraStreamRef.current.getAudioTracks());
    const recorder = new MediaRecorder(audioStream);
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) {
        audioChunksRef.current.push(event.data);
      }
    };
    recorder.onstop = () => uploadVoiceBlob(liveTranscriptRef.current);
    mediaRecorderRef.current = recorder;
    recorder.start();
    void logLiveTurnEvent("recording_started", session?.current_question?.id, {});

    const Recognition = window.SpeechRecognition ?? window.webkitSpeechRecognition;
    if (!Recognition) {
      setStatus("Recording audio. Server transcription will run after you stop.");
      setRecording(true);
      return;
    }
    const recognition = new Recognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "en-IN";
    recognition.onresult = (event) => {
      let text = "";
      for (let index = 0; index < event.results.length; index += 1) {
        text += event.results[index][0].transcript;
      }
      setLiveTranscript(text.trim());
    };
    recognition.onend = () => setRecording(false);
    recognitionRef.current = recognition;
    recognition.start();
    setRecording(true);
  }

  async function uploadVoiceBlob(draftTranscript: string) {
    if (!session || audioChunksRef.current.length === 0) {
      return;
    }
    await logLiveTurnEvent("recording_stopped", session.current_question?.id, { draft_transcript_length: draftTranscript.trim().length });
    setServerTranscriptStatus("Uploading audio for server transcription...");
    const blob = new Blob(audioChunksRef.current, { type: "audio/webm" });
    const form = new FormData();
    form.append("audio", blob, "answer.webm");
    form.append("draft_transcript", draftTranscript);
    try {
      const response = await fetch(`${API_BASE}/api/student/attempts/current/audio`, {
        method: "POST",
        credentials: "include",
        headers: { "X-CSRF-Token": window.localStorage.getItem("twelve_csrf") ?? "" },
        body: form
      });
      if (response.ok) {
        const data = await response.json() as {
          audio_ref: string;
          transcription_status: string;
          transcription_provider: string;
          transcription_model?: string | null;
          transcript_text?: string;
          transcription_error?: string | null;
        };
        setPendingAudioRef(data.audio_ref);
        if (data.transcript_text) {
          setLiveTranscript(data.transcript_text);
          await logLiveTurnEvent("server_transcript_received", session.current_question?.id, {
            status: data.transcription_status,
            provider: data.transcription_provider,
            model: data.transcription_model,
            text_length: data.transcript_text.length
          });
        }
        setServerTranscriptStatus(
          data.transcription_status === "transcribed" || data.transcription_status === "draft_used"
            ? `Server transcript ready (${data.transcription_provider}).`
            : data.transcription_error || "Server transcription is pending or unavailable."
        );
      } else {
        const message = await response.text();
        setServerTranscriptStatus(message || "Voice audio could not be uploaded.");
      }
    } catch {
      setStatus("Voice audio could not be uploaded; transcript is still available.");
      setServerTranscriptStatus("Voice audio could not be uploaded.");
    }
  }

  async function analyzeCamera() {
    const video = videoRef.current;
    if (!video || video.readyState < 2) {
      await logEvent("no_face", { reason: "video_not_ready" }, 0.6, undefined, "info");
      return;
    }

    const canvas = document.createElement("canvas");
    canvas.width = 80;
    canvas.height = 60;
    const context = canvas.getContext("2d");
    if (!context) {
      return;
    }
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
    let brightness = 0;
    for (let index = 0; index < pixels.length; index += 4) {
      brightness += (pixels[index] + pixels[index + 1] + pixels[index + 2]) / 3;
    }
    brightness = brightness / (pixels.length / 4);
    if (brightness < 18) {
      await logEvent("camera_blocked", { brightness: Math.round(brightness) }, 0.82, undefined, "high");
    }

    if (!window.FaceDetector) {
      return;
    }
    const detector = new window.FaceDetector({ fastMode: true, maxDetectedFaces: 3 });
    const faces = await detector.detect(video);
    if (faces.length === 0) {
      await logEvent("no_face", { detector: "FaceDetector" }, 0.86, undefined, "warning");
      return;
    }
    if (faces.length > 1) {
      await logEvent("multiple_faces", { count: faces.length }, 0.9, undefined, "high");
    }
    const face = faces[0].boundingBox;
    const centerX = face.x + face.width / 2;
    const centerRatio = centerX / video.videoWidth;
    if (centerRatio < 0.28 || centerRatio > 0.72) {
      gazeMisses.current += 1;
      if (gazeMisses.current >= 2) {
        await logEvent("sustained_gaze_away", { centerRatio: Number(centerRatio.toFixed(2)) }, 0.65, 7000, "warning");
      }
    } else {
      gazeMisses.current = 0;
    }
  }

  const canStart = mediaActive && permissions.fullscreen && examId && rollNumber && oneTimeCode && consent;
  const currentQuestion: VivaQuestion | null = session?.current_question ?? null;

  return (
    <main className="shell">
      <div className="page-head">
        <div>
          <h1>Student Viva</h1>
          <p>Answer by voice or typing. Question text remains on screen exactly as asked, and proctoring signals are logged as review flags only.</p>
        </div>
      </div>

      <section className="grid two">
        <div className="panel">
          {!session && (
            <form onSubmit={startSession}>
              <h2 className="section-title">Session Start</h2>
              <div className="field">
                <label htmlFor="exam">Exam</label>
                <select id="exam" value={examId} onChange={(event) => setExamId(event.target.value)} required>
                  {exams.map((exam) => <option key={exam.id} value={exam.id}>{exam.name}</option>)}
                </select>
              </div>
              <div className="field">
                <label htmlFor="rollNumber">Roll number</label>
                <input id="rollNumber" value={rollNumber} onChange={(event) => setRollNumber(event.target.value)} required />
              </div>
              <div className="field">
                <label htmlFor="oneTimeCode">One-time exam code</label>
                <input id="oneTimeCode" value={oneTimeCode} onChange={(event) => setOneTimeCode(event.target.value)} required />
              </div>
              <div className="row" style={{ marginBottom: 14 }}>
                <button type="button" className={permissions.camera ? "secondary" : ""} onClick={requestCameraMic}>
                  <Camera size={17} /> Camera + mic
                </button>
                <button type="button" className={permissions.fullscreen ? "secondary" : ""} onClick={requestFullscreen}>
                  <Maximize2 size={17} /> Fullscreen
                </button>
                <button type="button" className={permissions.screen ? "secondary" : ""} onClick={requestScreen}>
                  <MonitorUp size={17} /> Screen share
                </button>
              </div>
              <div className="row" style={{ marginBottom: 14 }}>
                <Badge ok={permissions.camera}>Camera</Badge>
                <Badge ok={permissions.microphone}>Mic</Badge>
                <Badge ok={permissions.fullscreen}>Fullscreen</Badge>
                <Badge ok={permissions.screen}>Screen optional</Badge>
              </div>
              <label className="check-row">
                <input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
                <span>I consent to browser proctoring flags, audio recording, transcripts, AI scoring, and professor review for this viva.</span>
              </label>
              <button className="primary" disabled={!canStart} type="submit">Start viva</button>
            </form>
          )}

          {session && (
            <div>
              <div className="row" style={{ justifyContent: "space-between", marginBottom: 16 }}>
                <span className={session.status === "completed" ? "status ok" : "status warn"}>
                  {session.status === "completed" ? <CheckCircle2 size={16} /> : <PauseCircle size={16} />}
                  {session.status}
                </span>
                {session.final_score !== null && <span className="score">{session.final_score}%</span>}
              </div>
              {!mediaActive && session.status === "active" && (
                <div className="row" style={{ marginBottom: 14 }}>
                  <span className="status warn">Camera and mic need reacquisition</span>
                  <button type="button" className="secondary" onClick={requestCameraMic}>
                    <Camera size={17} /> Reacquire camera + mic
                  </button>
                </div>
              )}
              {currentQuestion ? (
                <>
                  <div className="row" style={{ marginBottom: 10 }}>
                    <span className="status">{currentQuestion.category}</span>
                    <span className={speaking ? "status warn" : "status"}>
                      <Volume2 size={16} /> {speaking ? "AI speaking" : "Question displayed"}
                    </span>
                  </div>
                  <p className="question">{currentQuestion.text}</p>
                  <div className="field" style={{ marginTop: 18 }}>
                    <label htmlFor="answer">Typed answer</label>
                    <textarea id="answer" value={answer} onChange={(event) => setAnswer(event.target.value)} />
                  </div>
                  <div className="field">
                    <label htmlFor="liveTranscript">Voice transcript</label>
                    <textarea id="liveTranscript" value={liveTranscript} onChange={(event) => setLiveTranscript(event.target.value)} />
                  </div>
                  <div className="row">
                    <button type="button" className={recording ? "danger" : ""} onClick={toggleVoice}>
                      <Mic size={17} /> {recording ? "Stop voice" : "Record voice"}
                    </button>
                    {pendingAudioRef && <span className="status ok">Audio stored</span>}
                    <button type="button" onClick={() => submitAnswer("voice")} disabled={submitting || !mediaActive || !pendingAudioRef || !liveTranscript.trim()}>
                      <Send size={17} /> Submit voice
                    </button>
                    <button type="button" className="primary" onClick={() => submitAnswer("typed")} disabled={submitting || !mediaActive || !answer.trim()}>
                      <Send size={17} /> {submitting ? "Submitting" : "Submit typed"}
                    </button>
                  </div>
                  {serverTranscriptStatus && <p className="small muted">{serverTranscriptStatus}</p>}
                </>
              ) : (
                <div>
                  <h2 className="section-title">All questions answered</h2>
                  <p className="muted">The session can now be finalized for professor review.</p>
                  <button className="primary" onClick={finalize} type="button">Finalize score</button>
                </div>
              )}
              {status && <p className="small muted">{status}</p>}
            </div>
          )}
        </div>

        <aside className="grid">
          <div className="panel">
            <h2 className="section-title">Camera Preview</h2>
            <div className="video-box">
              <video ref={videoRef} autoPlay muted playsInline />
            </div>
          </div>
          <div className="panel">
            <h2 className="section-title">Proctoring Flags</h2>
            <div className="timeline">
              {(session?.proctoring_events ?? []).slice(-8).map((event) => (
                <div className="timeline-item flag" key={event.id}>
                  <strong>{event.event_type}</strong>
                  <div className="muted small">{new Date(event.created_at).toLocaleTimeString()} · confidence {Math.round(event.confidence * 100)}%</div>
                </div>
              ))}
              {!session?.proctoring_events.length && <p className="muted small">No flags logged.</p>}
            </div>
          </div>
        </aside>
      </section>

      {session && (
        <section className="panel" style={{ marginTop: 16 }}>
          <h2 className="section-title">Transcript Preview</h2>
          <div className="timeline">
            {session.answers.map((item) => (
              <div className="timeline-item" key={item.id}>
                <strong>{item.score}/{item.max_score}</strong> · {item.reasoning}
                <p className="muted small">{item.answer_text}</p>
              </div>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}

function Badge({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return <span className={ok ? "status ok" : "status danger"}>{children}</span>;
}
