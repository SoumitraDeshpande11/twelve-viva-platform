"use client";

import { useCallback, useRef, useState } from "react";
import { LogEvent, MediaError, PermissionState } from "../types";

/** Translate a raw getUserMedia/getDisplayMedia error into actionable recovery guidance. */
function describeMediaError(error: unknown): MediaError {
  const name = error instanceof DOMException ? error.name : "";
  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
      return {
        kind: "denied",
        message:
          "Camera and mic are blocked. Click the camera icon in your browser's address bar, choose Allow, then Retry.",
      };
    case "NotFoundError":
    case "OverconstrainedError":
      return {
        kind: "notfound",
        message:
          "No camera or microphone was found. Connect a device (or check it isn't disabled), then Retry.",
      };
    case "NotReadableError":
    case "AbortError":
      return {
        kind: "inuse",
        message:
          "Your camera or mic is in use by another app (Zoom, Meet, another tab). Close it, then Retry.",
      };
    default:
      return {
        kind: "unknown",
        message:
          error instanceof Error && error.message
            ? `Camera and mic could not be opened: ${error.message}. Try Retry.`
            : "Camera and mic could not be opened. Try Retry.",
      };
  }
}

/**
 * Camera / mic / screen / fullscreen acquisition and track-loss monitoring.
 * Emits proctoring flags through the injected logEvent; surfaces human-readable
 * problems through notify, and structured recovery guidance through mediaError.
 */
export function useMediaCapture(logEvent: LogEvent, notify: (message: string) => void) {
  const [permissions, setPermissions] = useState<PermissionState>({
    camera: false,
    microphone: false,
    fullscreen: false,
    screen: false,
  });
  const [mediaActive, setMediaActive] = useState(false);
  const [mediaError, setMediaError] = useState<MediaError | null>(null);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const cameraStreamRef = useRef<MediaStream | null>(null);
  const screenStreamRef = useRef<MediaStream | null>(null);

  const requestCameraMic = useCallback(async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      setMediaActive(false);
      setMediaError({
        kind: "unsupported",
        message:
          "This browser does not support camera/mic capture. Use a recent Chrome, Edge, or Firefox.",
      });
      return null;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
      cameraStreamRef.current = stream;
      if (videoRef.current) videoRef.current.srcObject = stream;
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
      setMediaError(null);
      setPermissions((current) => ({ ...current, camera: true, microphone: true }));
      notify("");
      return stream;
    } catch (error) {
      const described = describeMediaError(error);
      setMediaActive(false);
      setMediaError(described);
      setPermissions((current) => ({ ...current, camera: false, microphone: false }));
      notify("");
      return null;
    }
  }, [logEvent, notify]);

  const requestScreen = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
      screenStreamRef.current = stream;
      stream.getVideoTracks()[0].onended = () => {
        setPermissions((current) => ({ ...current, screen: false }));
        logEvent("screen_share_stopped", {}, 1, undefined, "high");
      };
      setPermissions((current) => ({ ...current, screen: true }));
      notify("");
    } catch (error) {
      setPermissions((current) => ({ ...current, screen: false }));
      notify(error instanceof Error ? error.message : "Screen share could not be started.");
    }
  }, [logEvent, notify]);

  const requestFullscreen = useCallback(async () => {
    try {
      await document.documentElement.requestFullscreen();
      setPermissions((current) => ({ ...current, fullscreen: true }));
      notify("");
    } catch (error) {
      setPermissions((current) => ({ ...current, fullscreen: false }));
      notify(error instanceof Error ? error.message : "Fullscreen could not be started.");
    }
  }, [notify]);

  /** Called by the proctoring fullscreenchange listener to keep the badge in sync. */
  const setFullscreenActive = useCallback((active: boolean) => {
    setPermissions((current) => ({ ...current, fullscreen: active }));
  }, []);

  /** Ensure a camera/mic stream exists, acquiring on demand. */
  const ensureCameraMic = useCallback(async () => {
    if (cameraStreamRef.current) return cameraStreamRef.current;
    return requestCameraMic();
  }, [requestCameraMic]);

  return {
    permissions,
    mediaActive,
    mediaError,
    videoRef,
    cameraStreamRef,
    screenStreamRef,
    requestCameraMic,
    requestScreen,
    requestFullscreen,
    setFullscreenActive,
    ensureCameraMic,
  };
}
