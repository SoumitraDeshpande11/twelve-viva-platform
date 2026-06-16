"use client";

import { RefObject, useEffect, useRef, useState } from "react";

export type LightingStatus =
  | "idle" // no camera yet
  | "checking" // first sample pending
  | "ok" // well lit
  | "dark" // too dark to see the face
  | "bright"; // washed out / strong backlight

const CHECK_INTERVAL_MS = 2000;
const SUSTAINED_MISSES = 2; // ~4s before nagging, so a brief shadow doesn't warn
const DARK_BELOW = 40; // mean luma 0-255
const BRIGHT_ABOVE = 235;
const SAMPLE_W = 64;
const SAMPLE_H = 48;

/**
 * Lightweight lighting check on the camera preview: samples the average brightness of a
 * tiny downscaled frame (no face/ML detection) and reports whether the face is well lit.
 * Cheap enough to run before and during the viva. Detection-only — never affects score.
 */
export function useFaceLighting(
  videoRef: RefObject<HTMLVideoElement | null>,
  enabled: boolean
): { status: LightingStatus; warn: boolean } {
  const [status, setStatus] = useState<LightingStatus>("idle");
  const [warn, setWarn] = useState(false);
  const missesRef = useRef(0);

  useEffect(() => {
    if (!enabled) {
      setStatus("idle");
      setWarn(false);
      missesRef.current = 0;
      return;
    }

    let cancelled = false;
    const canvas = document.createElement("canvas");
    canvas.width = SAMPLE_W;
    canvas.height = SAMPLE_H;
    const context = canvas.getContext("2d", { willReadFrequently: true });

    const miss = (next: LightingStatus) => {
      setStatus(next);
      missesRef.current += 1;
      if (missesRef.current >= SUSTAINED_MISSES) setWarn(true);
    };

    const tick = () => {
      const video = videoRef.current;
      if (!video || video.readyState < 2 || !context) return;
      context.drawImage(video, 0, 0, SAMPLE_W, SAMPLE_H);
      let sum = 0;
      const { data } = context.getImageData(0, 0, SAMPLE_W, SAMPLE_H);
      for (let i = 0; i < data.length; i += 4) {
        // Rec. 601 luma approximation.
        sum += 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
      }
      if (cancelled) return;
      const mean = sum / (data.length / 4);
      if (mean < DARK_BELOW) {
        miss("dark");
      } else if (mean > BRIGHT_ABOVE) {
        miss("bright");
      } else {
        setStatus("ok");
        missesRef.current = 0;
        setWarn(false);
      }
    };

    setStatus("checking");
    const interval = window.setInterval(tick, CHECK_INTERVAL_MS);
    tick();
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [enabled, videoRef]);

  return { status, warn };
}
