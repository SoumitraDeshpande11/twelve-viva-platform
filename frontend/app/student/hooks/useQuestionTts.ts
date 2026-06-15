"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE, VivaQuestion, VivaSession } from "../../../lib/api";
import { LogLiveTurn } from "../types";

/**
 * Speaks the displayed question: server-rendered TTS (Gemini WAV) when available,
 * falling back to the browser speechSynthesis. The question text always stays on
 * screen exactly as posed — audio is an aid, not the source of truth.
 */
export function useQuestionTts(session: VivaSession | null, logLiveTurnEvent: LogLiveTurn) {
  const [speaking, setSpeaking] = useState(false);
  // Whether playback is currently paused (server <audio> or browser speechSynthesis).
  const [paused, setPaused] = useState(false);
  const sessionRef = useRef<VivaSession | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  // Object URL backing the current TTS <audio>; revoked on replace/ended to avoid leaks.
  const audioUrlRef = useRef<string | null>(null);

  useEffect(() => {
    sessionRef.current = session;
  }, [session]);

  /** Tear down the active server-TTS audio element and free its object URL. */
  const releaseAudio = useCallback(() => {
    audioRef.current?.pause();
    audioRef.current = null;
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = null;
    }
  }, []);

  const speakWithBrowser = useCallback(
    (text: string, questionId?: string) => {
      if (!("speechSynthesis" in window)) {
        setSpeaking(false);
        return;
      }
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = 0.92;
      utterance.onstart = () => {
        setSpeaking(true);
        setPaused(false);
      };
      utterance.onend = () => {
        setSpeaking(false);
        setPaused(false);
        if (questionId) void logLiveTurnEvent("tts_completed", questionId, { provider: "browser" });
      };
      window.speechSynthesis.speak(utterance);
    },
    [logLiveTurnEvent]
  );

  const speakQuestion = useCallback(
    async (question: VivaQuestion) => {
      setSpeaking(true);
      setPaused(false);
      window.speechSynthesis?.cancel();
      releaseAudio();

      const current = sessionRef.current;
      if (current) {
        try {
          await logLiveTurnEvent("tts_started", question.id, { provider: "server-or-browser" });
          const response = await fetch(
            `${API_BASE}/api/sessions/${current.id}/questions/${question.id}/tts`,
            { cache: "no-store", credentials: "include" }
          );
          if (response.ok) {
            const blob = await response.blob();
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);
            audioRef.current = audio;
            audioUrlRef.current = url;
            audio.onended = () => {
              setSpeaking(false);
              setPaused(false);
              releaseAudio();
              void logLiveTurnEvent("tts_completed", question.id, { provider: "gemini" });
            };
            audio.onerror = () => {
              setSpeaking(false);
              setPaused(false);
              releaseAudio();
              speakWithBrowser(question.text, question.id);
            };
            await audio.play();
            return;
          }
        } catch {
          // Fall through to browser speech synthesis.
        }
      }
      speakWithBrowser(question.text, question.id);
    },
    [logLiveTurnEvent, releaseAudio, speakWithBrowser]
  );

  /** Pause/resume the current question audio (server <audio> or browser speech). */
  const togglePlayback = useCallback(() => {
    const audio = audioRef.current;
    if (audio) {
      if (audio.paused) {
        void audio.play();
        setPaused(false);
      } else {
        audio.pause();
        setPaused(true);
      }
      return;
    }
    const synth = window.speechSynthesis;
    if (synth?.speaking) {
      if (synth.paused) {
        synth.resume();
        setPaused(false);
      } else {
        synth.pause();
        setPaused(true);
      }
    }
  }, []);

  return { speaking, paused, speakQuestion, togglePlayback };
}
