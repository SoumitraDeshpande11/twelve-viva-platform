"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ArrowsClockwise as RefreshCcw,
  ArrowLeft,
  CaretRight,
  Users,
} from "@phosphor-icons/react";
import {
  getMe,
  logout,
  getExamClass,
  type ExamClass,
  type ClassRosterEntry,
} from "../../../lib/api";
import { cn } from "../../../lib/cn";
import { AuthPanel } from "../../AuthPanel";
import { PageHeading } from "../../../components/AppShell";
import { Card, SectionTitle } from "../../../components/ui/Card";
import { Button } from "../../../components/ui/Button";
import { Banner } from "../../../components/ui/Banner";
import { StatusPill } from "../../../components/ui/Pill";
import { SessionDetail } from "../SessionDetail";

const MARK_MODE_LABELS: Record<string, string> = {
  ai_official: "AI official",
  professor_approved: "Professor approved",
};

function statusTone(status: string): "ok" | "warn" | "neutral" {
  if (status === "completed") return "ok";
  if (status === "active") return "warn";
  return "neutral";
}

function rosterScore(entry: ClassRosterEntry): number | null {
  return entry.effective_score ?? entry.final_score ?? null;
}

export default function ExamClassPage() {
  const params = useParams<{ examId: string }>();
  const examId = params.examId;

  const [data, setData] = useState<ExamClass | null>(null);
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [needsAuth, setNeedsAuth] = useState(false);
  // Why auth is needed: "none" = not signed in, "student" = signed in as a student
  // (review is staff-only, so they must switch).
  const [authReason, setAuthReason] = useState<"none" | "student">("none");
  const [refreshing, setRefreshing] = useState(false);

  async function loadClass() {
    setRefreshing(true);
    try {
      const next = await getExamClass(examId);
      setData(next);
      // Keep the open student only if they still have a session; otherwise close.
      setSelectedSessionId((prev) =>
        prev && next.roster.some((r) => r.session_id === prev) ? prev : null
      );
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    if (!examId) return;
    getMe()
      .then((me) => {
        if (me.role === "staff") {
          loadClass().catch((error) => setMessage(error.message));
        } else {
          setAuthReason("student");
          setNeedsAuth(true);
        }
      })
      .catch(() => {
        setAuthReason("none");
        setNeedsAuth(true);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [examId]);

  async function signOutToSwitch() {
    try {
      await logout();
    } catch {
      // best-effort; the AuthPanel login below will overwrite the cookie regardless.
    }
    setAuthReason("none");
  }

  if (needsAuth) {
    return (
      <div className="flex flex-col gap-4">
        {authReason === "student" && (
          <Banner tone="warn">
            <p className="font-medium">You&apos;re signed in as a student.</p>
            <p className="mt-0.5">
              Review is for staff only. Sign in with a staff account below to see this class —
              that replaces the student session in this browser.
            </p>
            <Button size="sm" variant="secondary" className="mt-2.5" onClick={signOutToSwitch}>
              Sign out of student session
            </Button>
          </Banner>
        )}
        <AuthPanel
          onReady={() => {
            setNeedsAuth(false);
            loadClass().catch((error) => setMessage(error.message));
          }}
        />
      </div>
    );
  }

  const markLabel = data?.exam.mark_mode
    ? MARK_MODE_LABELS[data.exam.mark_mode] ?? data.exam.mark_mode
    : null;

  return (
    <div>
      <Link
        href="/review"
        className="reveal mb-4 inline-flex items-center gap-1.5 text-[0.82rem] font-medium text-ink-soft transition-colors hover:text-accent"
      >
        <ArrowLeft size={15} /> Back to exams
      </Link>

      <PageHeading
        eyebrow="№ 03 · Class roster"
        title={data?.exam.name ?? "Loading class…"}
        action={
          <Button onClick={() => loadClass()} disabled={refreshing} aria-busy={refreshing} aria-label="Refresh class">
            <RefreshCcw size={16} className={refreshing ? "animate-spin" : undefined} />
            {refreshing ? "Refreshing…" : "Refresh"}
          </Button>
        }
      >
        {data ? (
          <span className="inline-flex flex-wrap items-center gap-2">
            <StatusPill tone="neutral">
              <Users size={13} /> {data.taken_count}/{data.student_count} taken
            </StatusPill>
            <StatusPill tone="ok">{data.completed_count} completed</StatusPill>
            {markLabel && <StatusPill tone="neutral">{markLabel}</StatusPill>}
            {data.exam.archived && <StatusPill tone="neutral">Archived</StatusPill>}
          </span>
        ) : (
          "Loading the whole class roster…"
        )}
      </PageHeading>

      {message && (
        <div className="mb-4">
          <Banner tone="danger">{message}</Banner>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[0.85fr_1.55fr]">
        {/* ── Class roster ─────────────────────────── */}
        <Card className="reveal h-fit p-5">
          <SectionTitle marker="№ —" title="Class roster" hint="Every enrolled student." />
          <div className="mt-4 flex flex-col gap-2">
            {(data?.roster ?? []).map((entry) => {
              const clickable = Boolean(entry.session_id);
              const score = rosterScore(entry);
              const active = entry.session_id && entry.session_id === selectedSessionId;
              const inner = (
                <>
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-[0.88rem] font-medium text-ink">{entry.name}</span>
                    {score != null && (
                      <span className="shrink-0 font-display text-[0.95rem] text-accent tnum">{score}%</span>
                    )}
                  </div>
                  <span className="text-[0.74rem] text-muted">
                    <span className="font-mono">{entry.roll_number}</span>
                  </span>
                  <div className="flex items-center justify-between gap-2">
                    <StatusPill tone={statusTone(entry.attempt_status)}>
                      {entry.attempt_status.replace(/_/g, " ")}
                    </StatusPill>
                    {clickable && (
                      <CaretRight size={14} className="text-muted" />
                    )}
                  </div>
                </>
              );
              const className = cn(
                "flex flex-col gap-1.5 rounded-[var(--radius-control)] border px-3.5 py-3 text-left transition-colors",
                active
                  ? "border-accent/50 bg-accent-soft"
                  : clickable
                    ? "border-line bg-surface-2/40 hover:border-accent/30"
                    : "border-line bg-surface-2/20 cursor-default"
              );
              return clickable ? (
                <button
                  key={entry.student_id}
                  type="button"
                  onClick={() => setSelectedSessionId(entry.session_id)}
                  className={className}
                >
                  {inner}
                </button>
              ) : (
                <div key={entry.student_id} className={className} aria-disabled>
                  {inner}
                </div>
              );
            })}
            {data && !data.roster.length && (
              <p className="text-[0.82rem] text-muted">No students enrolled in this exam.</p>
            )}
            {!data && !message && (
              <div className="flex items-center gap-2 text-[0.82rem] text-muted">
                <RefreshCcw size={14} className="animate-spin text-accent" /> Loading roster…
              </div>
            )}
          </div>
        </Card>

        {/* ── Detail ─────────────────────────── */}
        <div>
          {selectedSessionId ? (
            <SessionDetail key={selectedSessionId} sessionId={selectedSessionId} />
          ) : (
            <Card className="reveal flex h-fit items-center justify-center p-10">
              <p className="text-center text-[0.85rem] text-muted">
                Select a student with a viva attempt to read their full record.
              </p>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
