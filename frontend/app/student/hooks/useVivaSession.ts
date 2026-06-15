"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, Exam, makeIdempotencyKey, ProctoringEvent, VivaSession } from "../../../lib/api";
import { LogEvent, LogLiveTurn, PermissionState, Severity } from "../types";

const FLAG_DEBOUNCE_MS = 2500;

/**
 * Owns the viva session lifecycle: load existing attempt, start, submit answers,
 * finalize — plus the two server-logging primitives (proctoring + live-turn audit)
 * that every other hook needs. Reads the latest session through a ref so the
 * logging callbacks stay stable and never go stale.
 */
export function useVivaSession() {
  const [exams, setExams] = useState<Exam[]>([]);
  const [session, setSession] = useState<VivaSession | null>(null);
  const [status, setStatus] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  // True until the "restore existing attempt" probe settles, so the UI can show a
  // placeholder instead of flashing the start form on refresh.
  const [restoring, setRestoring] = useState(true);

  const sessionRef = useRef<VivaSession | null>(null);
  const lastFlagRef = useRef<Record<string, number>>({});

  // Mirror the latest session into a ref so the stable callbacks below can read
  // it from async handlers without re-binding on every change.
  useEffect(() => {
    sessionRef.current = session;
  }, [session]);

  useEffect(() => {
    api<Exam[]>("/api/public/exams")
      .then(setExams)
      .catch((error) => setStatus(error instanceof Error ? error.message : "Could not load exams."));
    api<VivaSession>("/api/student/attempts/current")
      .then(setSession)
      .catch(() => undefined)
      .finally(() => setRestoring(false));
  }, []);

  const logEvent: LogEvent = useCallback(
    async (eventType, details, confidence, durationMs, severity: Severity = "warning") => {
      const current = sessionRef.current;
      if (!current) return;
      const key = `${eventType}:${current.current_question?.id ?? "none"}`;
      const now = Date.now();
      if ((lastFlagRef.current[key] ?? 0) > now - FLAG_DEBOUNCE_MS) return;
      lastFlagRef.current[key] = now;
      try {
        const event = await api<ProctoringEvent>("/api/student/attempts/current/proctoring-events", {
          method: "POST",
          body: JSON.stringify({ event_type: eventType, details, confidence, duration_ms: durationMs, severity }),
        });
        setSession((prev) =>
          prev ? { ...prev, proctoring_events: [...prev.proctoring_events, event] } : prev
        );
      } catch {
        setStatus("Proctoring event could not be logged; network may be unavailable.");
      }
    },
    []
  );

  const logLiveTurnEvent: LogLiveTurn = useCallback(async (eventType, questionId, payload = {}) => {
    if (!sessionRef.current) return;
    try {
      await api("/api/student/attempts/current/live-turn-events", {
        method: "POST",
        body: JSON.stringify({ event_type: eventType, question_id: questionId, payload }),
      });
    } catch {
      // Live-turn events are audit helpers; answer/proctoring flows handle their own failures.
    }
  }, []);

  const startSession = useCallback(
    async (
      examId: string,
      rollNumber: string,
      oneTimeCode: string,
      permissions: PermissionState,
      consentText: string
    ) => {
      setStatus("");
      try {
        const data = await api<VivaSession>("/api/student/attempts/start", {
          method: "POST",
          body: JSON.stringify({
            exam_id: examId,
            roll_number: rollNumber,
            one_time_code: oneTimeCode,
            permissions,
            consent_text: consentText,
          }),
        });
        setSession(data);
        return true;
      } catch (error) {
        setStatus(error instanceof Error ? error.message : "Unable to start session.");
        return false;
      }
    },
    []
  );

  const submitAnswer = useCallback(
    async (mode: "voice" | "typed", answerText: string, audioRef: string | null) => {
      const current = sessionRef.current;
      if (!current?.current_question) return false;
      const trimmed = answerText.trim();
      if (!trimmed) {
        setStatus("Answer cannot be empty.");
        return false;
      }
      setSubmitting(true);
      setStatus("");
      const questionId = current.current_question.id;
      try {
        await logLiveTurnEvent("answer_submit_started", questionId, { input_mode: mode });
        const updated = await api<VivaSession>("/api/student/attempts/current/answers", {
          method: "POST",
          headers: { "Idempotency-Key": makeIdempotencyKey() },
          body: JSON.stringify({
            question_id: questionId,
            answer_text: trimmed,
            input_mode: mode,
            audio_ref: mode === "voice" ? audioRef : null,
          }),
        });
        await logLiveTurnEvent("answer_submit_completed", questionId, { input_mode: mode });
        setSession(updated);
        return true;
      } catch (error) {
        await logLiveTurnEvent("answer_submit_failed", questionId, { input_mode: mode });
        setStatus(error instanceof Error ? error.message : "Answer could not be submitted.");
        return false;
      } finally {
        setSubmitting(false);
      }
    },
    [logLiveTurnEvent]
  );

  const finalize = useCallback(async () => {
    const current = sessionRef.current;
    // Guard against double-POST and finalizing an already-completed session.
    if (!current || current.status === "completed" || finalizing) return;
    setFinalizing(true);
    setStatus("");
    try {
      setSession(await api<VivaSession>(`/api/sessions/${current.id}/finalize`, { method: "POST" }));
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Could not finalize the session.");
    } finally {
      setFinalizing(false);
    }
  }, [finalizing]);

  return {
    exams,
    session,
    status,
    setStatus,
    submitting,
    finalizing,
    restoring,
    logEvent,
    logLiveTurnEvent,
    startSession,
    submitAnswer,
    finalize,
  };
}
