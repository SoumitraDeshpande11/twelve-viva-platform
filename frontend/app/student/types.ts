export type PermissionState = {
  camera: boolean;
  microphone: boolean;
  fullscreen: boolean;
  screen: boolean;
};

export type Severity = "info" | "warning" | "high";

/** Actionable media-permission failure surfaced to the student with recovery guidance. */
export type MediaError = {
  kind: "denied" | "notfound" | "inuse" | "unsupported" | "unknown";
  message: string;
};

export type SpeechRecognitionLike = {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start: () => void;
  stop: () => void;
  onresult:
    | ((event: { results: ArrayLike<{ 0: { transcript: string }; isFinal: boolean }> }) => void)
    | null;
  onstart: (() => void) | null;
  onend: (() => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
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

/** LogEvent signature shared across the viva hooks. */
export type LogEvent = (
  eventType: string,
  details: Record<string, unknown>,
  confidence: number,
  durationMs?: number,
  severity?: Severity
) => Promise<void>;

export type LogLiveTurn = (
  eventType: string,
  questionId?: string,
  payload?: Record<string, unknown>
) => Promise<void>;
