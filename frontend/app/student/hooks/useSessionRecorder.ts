"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { uploadRecording, VivaSession } from "../../../lib/api";
import { LogEvent } from "../types";

// ~500 MB cap (mirrors the backend TWELVE_MAX_VIDEO_BYTES default). Stop recording if
// reached so memory and the final upload stay bounded.
const MAX_BYTES = 500 * 1024 * 1024;
const VIDEO_BITS_PER_SECOND = 2_500_000; // ~2.5 Mbps, paired with the 720p capture.
const TIMESLICE_MS = 5000;

/** Pick the best-supported WebM container/codec, or null if recording is unavailable. */
function pickMimeType(): string | null {
  if (typeof MediaRecorder === "undefined" || !MediaRecorder.isTypeSupported) return null;
  const candidates = [
    "video/webm;codecs=vp9,opus",
    "video/webm;codecs=vp8,opus",
    "video/webm",
  ];
  return candidates.find((type) => MediaRecorder.isTypeSupported(type)) ?? null;
}

/**
 * Records the student's camera (video + audio) for the whole viva so an examiner can
 * watch it back in /review. Review-only — never scored. Captures the EXISTING camera
 * stream (no second getUserMedia), buffers chunks, and uploads one WebM at the end.
 * A failed upload is retained and retryable so footage is never silently lost.
 */
export function useSessionRecorder({
  session,
  ensureCameraMic,
  logEvent,
}: {
  session: VivaSession | null;
  ensureCameraMic: () => Promise<MediaStream | null>;
  logEvent: LogEvent;
}) {
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const bytesRef = useRef(0);
  const mimeRef = useRef<string>("video/webm");
  // The session this recorder started for, so polling re-renders don't restart it.
  const startedForRef = useRef<string | null>(null);
  // Guards against a double upload (e.g. completion effect + unmount both firing).
  const uploadedRef = useRef(false);
  const [recording, setRecording] = useState(false);
  const [uploadFailed, setUploadFailed] = useState(false);
  const pendingBlobRef = useRef<Blob | null>(null);

  const doUpload = useCallback(
    async (blob: Blob) => {
      pendingBlobRef.current = blob;
      try {
        await uploadRecording(blob);
        pendingBlobRef.current = null;
        setUploadFailed(false);
      } catch {
        // Keep the blob so the student can retry; never lose the footage silently.
        setUploadFailed(true);
      }
    },
    []
  );

  const stopAndUpload = useCallback(async () => {
    const recorder = recorderRef.current;
    if (!recorder || uploadedRef.current) return;
    uploadedRef.current = true;
    await new Promise<void>((resolve) => {
      recorder.onstop = () => resolve();
      try {
        recorder.stop();
      } catch {
        resolve();
      }
    });
    setRecording(false);
    recorderRef.current = null;
    if (!chunksRef.current.length) return;
    const blob = new Blob(chunksRef.current, { type: mimeRef.current });
    chunksRef.current = [];
    await doUpload(blob);
  }, [doUpload]);

  const begin = useCallback(async () => {
    const mime = pickMimeType();
    if (!mime) {
      void logEvent("recording_unsupported", { reason: "MediaRecorder/WebM unavailable" }, 1, undefined, "info");
      return;
    }
    const stream = await ensureCameraMic();
    if (!stream) return; // camera unavailable; the media-loss flag is logged elsewhere.
    let recorder: MediaRecorder;
    try {
      recorder = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: VIDEO_BITS_PER_SECOND });
    } catch {
      void logEvent("recording_unsupported", { reason: "MediaRecorder ctor failed" }, 1, undefined, "info");
      return;
    }
    mimeRef.current = mime;
    chunksRef.current = [];
    bytesRef.current = 0;
    recorder.ondataavailable = (event) => {
      if (!event.data || event.data.size === 0) return;
      chunksRef.current.push(event.data);
      bytesRef.current += event.data.size;
      if (bytesRef.current >= MAX_BYTES) {
        void logEvent("recording_cap_reached", { bytes: bytesRef.current }, 1, undefined, "warning");
        void stopAndUpload();
      }
    };
    recorderRef.current = recorder;
    recorder.start(TIMESLICE_MS);
    setRecording(true);
  }, [ensureCameraMic, logEvent, stopAndUpload]);

  // Lifecycle: start once when the viva goes active; stop+upload when it completes.
  useEffect(() => {
    const status = session?.status;
    const id = session?.id;
    if (status === "active" && id && startedForRef.current !== id) {
      startedForRef.current = id;
      uploadedRef.current = false;
      void begin();
    }
    if (status === "completed") {
      void stopAndUpload();
    }
  }, [session?.status, session?.id, begin, stopAndUpload]);

  // On unmount (e.g. the student navigates away), flush whatever was captured.
  useEffect(() => {
    return () => {
      void stopAndUpload();
    };
  }, [stopAndUpload]);

  const retryUpload = useCallback(async () => {
    if (pendingBlobRef.current) await doUpload(pendingBlobRef.current);
  }, [doUpload]);

  return { recording, uploadFailed, retryUpload };
}
