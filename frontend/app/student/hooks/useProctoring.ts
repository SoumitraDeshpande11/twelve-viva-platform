"use client";

import { RefObject, useEffect, useRef } from "react";
import { VivaSession } from "../../../lib/api";
import { LogEvent } from "../types";

const ANALYZE_INTERVAL_MS = 3500;

/**
 * Browser-level proctoring: tab/visibility, window blur, fullscreen exit, and a
 * periodic camera analysis (brightness + FaceDetector presence/centering).
 * All findings are review flags only — never scored.
 */
export function useProctoring({
  session,
  videoRef,
  logEvent,
  onFullscreenChange,
}: {
  session: VivaSession | null;
  videoRef: RefObject<HTMLVideoElement | null>;
  logEvent: LogEvent;
  onFullscreenChange: (active: boolean) => void;
}) {
  const gazeMisses = useRef(0);
  const active = Boolean(session);

  useEffect(() => {
    if (!active) return;

    // Collapse rapid repeats of the same flag (e.g. a tab-switch fires blur AND
    // visibilitychange, and fidgeting can fire either many times a second). We keep
    // distinct event types but throttle identical ones so one action ≈ one flag.
    const lastFlag: Record<string, number> = {};
    const FLAG_THROTTLE_MS = 4000;
    const flag = (
      type: string,
      payload: Record<string, unknown>,
      confidence: number,
      ttl: number | undefined,
      severity: "info" | "warning" | "high"
    ) => {
      const now = performance.now();
      if (now - (lastFlag[type] ?? -Infinity) < FLAG_THROTTLE_MS) return;
      lastFlag[type] = now;
      void logEvent(type, payload, confidence, ttl, severity);
    };

    const onVisibility = () => {
      if (document.hidden) flag("tab_hidden", { state: "hidden" }, 1, undefined, "high");
    };
    const onBlur = () => flag("window_blur", { state: "blurred" }, 0.9, undefined, "warning");
    const onFullscreen = () => {
      const isActive = Boolean(document.fullscreenElement);
      onFullscreenChange(isActive);
      if (!isActive) flag("fullscreen_exit", {}, 1, undefined, "high");
    };

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
      if (!context) return;
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

      if (!window.FaceDetector) return;
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

    document.addEventListener("visibilitychange", onVisibility);
    document.addEventListener("fullscreenchange", onFullscreen);
    window.addEventListener("blur", onBlur);
    const interval = window.setInterval(analyzeCamera, ANALYZE_INTERVAL_MS);

    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      document.removeEventListener("fullscreenchange", onFullscreen);
      window.removeEventListener("blur", onBlur);
      window.clearInterval(interval);
    };
    // Re-bind when the session transitions to/from active.
  }, [active, logEvent, onFullscreenChange, videoRef]);
}
