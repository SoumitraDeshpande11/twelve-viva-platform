const CONFIGURED_API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

/**
 * In local dev, target the SAME host the user is browsing (localhost vs 127.0.0.1)
 * rather than the hardcoded one. Otherwise the page (e.g. localhost:3000) and the API
 * (127.0.0.1:8000) are different "sites", so the SameSite=Lax auth cookie is dropped on
 * fetches — the app then looks logged-out everywhere (no logout, empty review). A real
 * (non-local) NEXT_PUBLIC_API_BASE is left untouched for production deployments.
 */
function resolveApiBase(): string {
  if (typeof window === "undefined") return CONFIGURED_API_BASE;
  try {
    const configured = new URL(CONFIGURED_API_BASE);
    const configuredIsLocal = configured.hostname === "localhost" || configured.hostname === "127.0.0.1";
    const browsingIsLocal =
      window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1";
    if (configuredIsLocal && browsingIsLocal) {
      return `${window.location.protocol}//${window.location.hostname}:${configured.port || "8000"}`;
    }
  } catch {
    // Fall through to the configured base on any parse error.
  }
  return CONFIGURED_API_BASE;
}

export const API_BASE = resolveApiBase();

/** Shared severity union for proctoring/flag tones. Single source of truth. */
export type Severity = "info" | "warning" | "high";

/** Staff role identifiers (mirror backend require_staff role sets). */
export type StaffRole = "super_admin" | "exam_admin" | "examiner" | "invigilator";

/** Response shape of GET /api/auth/me. */
export type Me =
  | {
      role: "staff";
      user: { id: string; email: string; name: string };
      roles: StaffRole[];
      csrf_token?: string;
    }
  | {
      role: "student";
      csrf_token?: string;
      session_id?: string;
      student_id?: string;
    };

export type Exam = {
  id: string;
  name: string;
  problem_statement: string;
  curriculum: string;
  rubric: string;
  created_at: string;
  student_count?: number;
  session_count?: number;
  students?: Student[];
  submissions?: Submission[];
  status?: string;
  mark_mode?: "professor_approved" | "ai_official";
  starts_at?: string | null;
  ends_at?: string | null;
};

export type Student = {
  id: string;
  roll_number: string;
  name: string;
  email?: string;
  token?: string;
};

export type Submission = {
  id: string;
  student_id?: string;
  filename: string;
  mime_type?: string;
  created_at: string;
};

export type VivaQuestion = {
  id: string;
  session_id: string;
  ordinal: number;
  category: string;
  text: string;
  expected_points: string[];
  created_at: string;
};

export type VivaAnswer = {
  id: string;
  question_id: string;
  input_mode: "voice" | "typed";
  answer_text: string;
  score: number;
  max_score: number;
  reasoning: string;
  scoring_status?: string;
  scorer_provider?: string;
  rubric_breakdown?: Record<string, unknown>;
  expected_points_covered?: string[];
  expected_points_missed?: string[];
  concerns?: string[];
  audio_ref?: string | null;
  created_at: string;
};

export type ProctoringEvent = {
  id: string;
  question_id?: string | null;
  event_type: string;
  details: Record<string, unknown>;
  confidence: number;
  duration_ms?: number;
  severity?: Severity;
  created_at: string;
};

export type TranscriptEvent = {
  id: string;
  sequence?: number;
  type: string;
  payload: Record<string, unknown>;
  prev_hash?: string;
  event_hash?: string;
  created_at: string;
};

export type AudioSubmission = {
  id: string;
  question_id?: string | null;
  audio_ref: string;
  mime_type?: string | null;
  size_bytes: number;
  draft_transcript?: string | null;
  transcript_text?: string | null;
  transcription_status: string;
  transcription_provider: string;
  transcription_model?: string | null;
  created_at: string;
  transcribed_at?: string | null;
};

export type VivaSession = {
  id: string;
  exam_id: string;
  exam_name: string;
  student_name: string;
  roll_number: string;
  status: "active" | "completed" | string;
  final_score: number | null;
  effective_score?: number | null;
  score_overridden?: boolean;
  score_source?: "ai" | "professor_override" | string;
  override_reviewer?: string | null;
  override_reason?: string | null;
  mark_mode?: "professor_approved" | "ai_official" | string;
  score_official?: boolean;
  score_status?: "official" | "provisional" | string;
  started_at: string;
  ended_at?: string;
  questions: VivaQuestion[];
  answers: VivaAnswer[];
  current_question: VivaQuestion | null;
  proctoring_events: ProctoringEvent[];
  transcript_events: TranscriptEvent[];
  audio_submissions?: AudioSubmission[];
  proctoring_count?: number;
  csrf_token?: string;
};

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: HeadersInit = init?.body instanceof FormData ? { ...init.headers } : { "Content-Type": "application/json", ...init?.headers };
  const method = (init?.method ?? "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = getCsrfToken();
    if (csrf) {
      (headers as Record<string, string>)["X-CSRF-Token"] = csrf;
    }
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
    credentials: "include",
    cache: "no-store"
  });
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed: ${response.status}`);
  }
  const data = await response.json() as T & { csrf_token?: string };
  if (data?.csrf_token && typeof window !== "undefined") {
    window.localStorage.setItem("twelve_csrf", data.csrf_token);
  }
  return data as T;
}

export function getCsrfToken() {
  if (typeof window === "undefined") {
    return "";
  }
  return window.localStorage.getItem("twelve_csrf") ?? "";
}

/** Fetch the current authenticated identity (staff or student) and roles. */
export async function getMe() {
  return api<Me>("/api/auth/me");
}

/** Alias for getMe(); mirrors the backend `me` endpoint name. */
export const me = getMe;

/** Upload the full-viva webcam recording (review-only) for the current student attempt. */
export async function uploadRecording(blob: Blob) {
  const form = new FormData();
  form.append("recording", blob, "viva.webm");
  return api<{ recording_id: string; size_bytes: number }>(
    "/api/student/attempts/current/recording",
    { method: "POST", body: form }
  );
}

/** Log out the current session (clears auth + csrf cookies server-side). */
export async function logout() {
  return api<{ ok: boolean; role: "staff" | "student" | null }>("/api/auth/logout", { method: "POST" });
}

/** Invite an additional staff member (super_admin only). */
export async function createStaff(input: {
  email: string;
  name: string;
  password: string;
  roles: StaffRole[];
}) {
  return api<{ id: string; email: string; name: string; roles: StaffRole[] }>("/api/auth/staff", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function makeIdempotencyKey() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
