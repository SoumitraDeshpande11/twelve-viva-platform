"use client";

import { useEffect, useRef, useState } from "react";
import { AiHealth, getAiHealth } from "../../../lib/api";

const POLL_MS = 8000;

/**
 * Heartbeat for the AI examiner's health. Polls GET /api/ai/health so the viva UI can
 * tell the student when scoring has degraded to the local fallback (provider down), and
 * automatically flips back to "full" once the server re-probes the provider successfully.
 *
 * `recovered` pulses true on the degraded → full transition so the UI can briefly show a
 * "back to full AI" confirmation. Only meaningful while `enabled` (an active attempt).
 */
export function useAiHealth(enabled: boolean) {
  const [health, setHealth] = useState<AiHealth | null>(null);
  const [recovered, setRecovered] = useState(false);
  const wasDegraded = useRef(false);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;

    async function tick() {
      try {
        const next = await getAiHealth();
        if (cancelled) return;
        if (wasDegraded.current && !next.degraded) {
          setRecovered(true);
          setTimeout(() => !cancelled && setRecovered(false), 6000);
        }
        wasDegraded.current = next.degraded;
        setHealth(next);
      } catch {
        // Health probe is best-effort; a failed poll must never disrupt the viva.
      }
    }

    void tick();
    const id = setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [enabled]);

  return { health, degraded: health?.degraded ?? false, recovered };
}
