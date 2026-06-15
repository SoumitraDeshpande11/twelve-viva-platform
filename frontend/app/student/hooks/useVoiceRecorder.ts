"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, VivaSession } from "../../../lib/api";
import { LogLiveTurn, SpeechRecognitionLike } from "../types";

/**
 * Records a spoken answer: MediaRecorder captures audio while the browser
 * SpeechRecognition (when present) produces a live draft. On stop, the audio is
 * uploaded for authoritative server-side transcription; the returned audio_ref is
 * what the answer submission references.
 */
export function useVoiceRecorder({
  session,
  ensureCameraMic,
  logLiveTurnEvent,
  notify,
}: {
  session: VivaSession | null;
  ensureCameraMic: () => Promise<MediaStream | null>;
  logLiveTurnEvent: LogLiveTurn;
  notify: (message: string) => void;
}) {
  const [recording, setRecording] = useState(false);
  const [liveTranscript, setLiveTranscript] = useState("");
  // Server-side audio_ref id for the last uploaded recording (state, not a React ref).
  const [audioId, setAudioId] = useState<string | null>(null);
  // True while a recorded blob is held locally awaiting (a possibly retried) upload.
  const [uploadFailed, setUploadFailed] = useState(false);
  const [serverTranscriptStatus, setServerTranscriptStatus] = useState("");

  const sessionRef = useRef<VivaSession | null>(null);
  const liveTranscriptRef = useRef("");
  // The recorded blob is retained here so an upload network failure never loses audio.
  const pendingBlobRef = useRef<Blob | null>(null);

  useEffect(() => {
    sessionRef.current = session;
  }, [session]);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<BlobPart[]>([]);

  useEffect(() => {
    liveTranscriptRef.current = liveTranscript;
  }, [liveTranscript]);

  const resetForQuestion = useCallback(() => {
    setLiveTranscript("");
    setAudioId(null);
    setUploadFailed(false);
    setServerTranscriptStatus("");
    pendingBlobRef.current = null;
  }, []);

  const uploadVoiceBlob = useCallback(
    async (draftTranscript: string) => {
      const current = sessionRef.current;
      const blob = pendingBlobRef.current;
      if (!current || !blob) return;
      setUploadFailed(false);
      setServerTranscriptStatus("Uploading audio for server transcription…");
      const form = new FormData();
      form.append("audio", blob, "answer.webm");
      form.append("draft_transcript", draftTranscript);
      try {
        const response = await fetch(`${API_BASE}/api/student/attempts/current/audio`, {
          method: "POST",
          credentials: "include",
          headers: { "X-CSRF-Token": window.localStorage.getItem("twelve_csrf") ?? "" },
          body: form,
        });
        if (response.ok) {
          const data = (await response.json()) as {
            audio_ref: string;
            transcription_status: string;
            transcription_provider: string;
            transcription_model?: string | null;
            transcript_text?: string;
            transcription_error?: string | null;
          };
          setAudioId(data.audio_ref);
          // Upload succeeded — the blob is now persisted server-side, drop the local copy.
          pendingBlobRef.current = null;
          if (data.transcript_text) {
            setLiveTranscript(data.transcript_text);
            await logLiveTurnEvent("server_transcript_received", current.current_question?.id, {
              status: data.transcription_status,
              provider: data.transcription_provider,
              model: data.transcription_model,
              text_length: data.transcript_text.length,
            });
          }
          setServerTranscriptStatus(
            data.transcription_status === "transcribed" || data.transcription_status === "draft_used"
              ? `Server transcript ready (${data.transcription_provider}).`
              : data.transcription_error || "Server transcription is pending or unavailable."
          );
        } else {
          // Keep the blob so the student can retry; mark the failure for the UI.
          setUploadFailed(true);
          setServerTranscriptStatus(
            (await response.text()) || "Upload failed. Your recording is kept — Retry upload."
          );
        }
      } catch {
        setUploadFailed(true);
        notify("");
        setServerTranscriptStatus("Upload failed (network). Your recording is kept — Retry upload.");
      }
    },
    [logLiveTurnEvent, notify]
  );

  /** Re-attempt the last failed upload using the retained blob. */
  const retryUpload = useCallback(async () => {
    if (!pendingBlobRef.current) return;
    await uploadVoiceBlob(liveTranscriptRef.current);
  }, [uploadVoiceBlob]);

  const toggleVoice = useCallback(async () => {
    if (recording) {
      recognitionRef.current?.stop();
      mediaRecorderRef.current?.stop();
      setRecording(false);
      return;
    }

    const stream = await ensureCameraMic();
    if (!stream) {
      notify("Grant camera and mic before recording.");
      return;
    }
    if (!("MediaRecorder" in window)) {
      notify("Media recording is not available in this browser. Use the typed answer fallback.");
      return;
    }

    setServerTranscriptStatus("");
    setAudioId(null);
    setUploadFailed(false);
    pendingBlobRef.current = null;
    audioChunksRef.current = [];

    const audioStream = new MediaStream(stream.getAudioTracks());
    const recorder = new MediaRecorder(audioStream);
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) audioChunksRef.current.push(event.data);
    };
    // The recording UI state is driven by the MediaRecorder lifecycle — NOT by
    // SpeechRecognition.onend, which Chrome fires on silence while audio is still
    // being captured. This keeps the button in sync with what is actually recorded.
    recorder.onstart = () => {
      setRecording(true);
      void logLiveTurnEvent("recording_started", sessionRef.current?.current_question?.id, {});
    };
    recorder.onstop = () => {
      setRecording(false);
      // Stop any still-running recognition so its mic handle is released.
      recognitionRef.current?.stop();
      recognitionRef.current = null;
      const chunks = audioChunksRef.current;
      pendingBlobRef.current = chunks.length ? new Blob(chunks, { type: "audio/webm" }) : null;
      void logLiveTurnEvent("recording_stopped", sessionRef.current?.current_question?.id, {
        draft_transcript_length: liveTranscriptRef.current.trim().length,
      });
      void uploadVoiceBlob(liveTranscriptRef.current);
    };
    mediaRecorderRef.current = recorder;
    recorder.start();

    const Recognition = window.SpeechRecognition ?? window.webkitSpeechRecognition;
    if (!Recognition) {
      notify("Recording audio. Server transcription will run after you stop.");
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
    recognition.onstart = null;
    // Chrome auto-restarts the draft while MediaRecorder is still live; do not touch
    // the recording state here — that is owned by the recorder lifecycle above.
    recognition.onend = () => {
      if (mediaRecorderRef.current?.state === "recording") {
        try {
          recognition.start();
        } catch {
          // Already restarting or stopped — the recorder remains the source of truth.
        }
      }
    };
    recognition.onerror = null;
    recognitionRef.current = recognition;
    try {
      recognition.start();
    } catch {
      // Recognition is best-effort; server transcription is the scored source of truth.
    }
  }, [recording, ensureCameraMic, logLiveTurnEvent, notify, uploadVoiceBlob]);

  return {
    recording,
    liveTranscript,
    setLiveTranscript,
    // True once a recording has been uploaded and the server holds an audio_ref to
    // transcribe — this is what gates voice submission (transcript may still be empty).
    hasUploadedAudio: audioId !== null,
    audioId,
    uploadFailed,
    serverTranscriptStatus,
    toggleVoice,
    retryUpload,
    resetForQuestion,
  };
}
