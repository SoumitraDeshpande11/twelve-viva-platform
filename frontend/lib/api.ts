export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

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
  severity?: "info" | "warning" | "high";
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

export function makeIdempotencyKey() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
