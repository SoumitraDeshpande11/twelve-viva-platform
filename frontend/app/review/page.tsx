"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowsClockwise as RefreshCcw,
  CaretRight,
  Archive,
  ArrowCounterClockwise as Restore,
  Users,
} from "@phosphor-icons/react";
import { getMe, logout, listReviewExams, archiveExam, unarchiveExam, type ReviewExam } from "../../lib/api";
import { cn } from "../../lib/cn";
import { AuthPanel } from "../AuthPanel";
import { PageHeading } from "../../components/AppShell";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Banner } from "../../components/ui/Banner";
import { StatusPill } from "../../components/ui/Pill";

const MARK_MODE_LABELS: Record<string, string> = {
  ai_official: "AI official",
  professor_approved: "Professor approved",
};

export default function ReviewPage() {
  const [exams, setExams] = useState<ReviewExam[]>([]);
  const [message, setMessage] = useState("");
  const [needsAuth, setNeedsAuth] = useState(false);
  // Why auth is needed: "none" = not signed in, "student" = signed in as a student
  // (review is staff-only, so they must switch). Drives a clear message vs a bare login.
  const [authReason, setAuthReason] = useState<"none" | "student">("none");
  const [refreshing, setRefreshing] = useState(false);
  const [showArchived, setShowArchived] = useState(false);
  // Archiving is an admin action; examiners just view. Drives whether the archive
  // button shows on each exam card.
  const [canManage, setCanManage] = useState(false);
  const [archiveBusyId, setArchiveBusyId] = useState<string | null>(null);

  async function loadExams(includeArchived = showArchived) {
    setRefreshing(true);
    try {
      setExams(await listReviewExams(includeArchived));
    } finally {
      setRefreshing(false);
    }
  }

  async function toggleArchive(exam: ReviewExam) {
    setArchiveBusyId(exam.id);
    setMessage("");
    try {
      if (exam.archived) await unarchiveExam(exam.id);
      else await archiveExam(exam.id);
      await loadExams();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Failed to update exam.");
    } finally {
      setArchiveBusyId(null);
    }
  }

  useEffect(() => {
    // Review is staff-only. Distinguish "not signed in" from "signed in as a student"
    // (the common case after testing the viva) so the page explains the fix instead of
    // showing an empty list or a confusing bare login.
    getMe()
      .then((me) => {
        if (me.role === "staff") {
          setCanManage(me.roles.includes("super_admin") || me.roles.includes("exam_admin"));
          loadExams(false).catch((error) => setMessage(error.message));
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
  }, []);

  async function toggleArchived() {
    const next = !showArchived;
    setShowArchived(next);
    try {
      await loadExams(next);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Failed to load exams.");
    }
  }

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
              Review is for staff only. Sign in with a staff account below to see viva
              sessions — that replaces the student session in this browser.
            </p>
            <Button size="sm" variant="secondary" className="mt-2.5" onClick={signOutToSwitch}>
              Sign out of student session
            </Button>
          </Banner>
        )}
        <AuthPanel
          onReady={() => {
            setNeedsAuth(false);
            loadExams(false).catch((error) => setMessage(error.message));
          }}
        />
      </div>
    );
  }

  return (
    <div>
      <PageHeading
        eyebrow="№ 03 · Professor Review"
        title="The class ledger."
        action={
          <div className="flex items-center gap-2">
            <Button
              variant={showArchived ? "primary" : "secondary"}
              onClick={toggleArchived}
              aria-pressed={showArchived}
              aria-label="Toggle archived exams"
            >
              <Archive size={16} />
              {showArchived ? "Hide archived" : "Show archived"}
            </Button>
            <Button onClick={() => loadExams()} disabled={refreshing} aria-busy={refreshing} aria-label="Refresh exams">
              <RefreshCcw size={16} className={refreshing ? "animate-spin" : undefined} />
              {refreshing ? "Refreshing…" : "Refresh"}
            </Button>
          </div>
        }
      >
        Pick an exam to open its class roster, then drill into any student&apos;s viva record. An
        override stores a new record without disturbing the AI score.
      </PageHeading>

      {message && (
        <div className="mb-4">
          <Banner tone="danger">{message}</Banner>
        </div>
      )}

      <div className="flex flex-col gap-3">
        {exams.map((exam) => {
          const markLabel = exam.mark_mode ? MARK_MODE_LABELS[exam.mark_mode] ?? exam.mark_mode : null;
          return (
            <Card
              key={exam.id}
              className={cn(
                "reveal flex items-center gap-2 p-0 transition-colors hover:border-accent/40",
                exam.archived && "opacity-70"
              )}
            >
              <Link
                href={`/review/${exam.id}`}
                className="group flex min-w-0 flex-1 items-center justify-between gap-4 p-5"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="truncate text-[1.15rem] text-ink">{exam.name}</h2>
                    {exam.archived && (
                      <StatusPill tone="neutral">
                        <Archive size={12} /> Archived
                      </StatusPill>
                    )}
                    {markLabel && <StatusPill tone="neutral">{markLabel}</StatusPill>}
                    {exam.status && <StatusPill tone="neutral">{exam.status}</StatusPill>}
                  </div>
                  <p className="mt-1.5 text-[0.78rem] text-muted">
                    {exam.completed_count} completed
                    {exam.created_at ? ` · created ${new Date(exam.created_at).toLocaleDateString()}` : ""}
                  </p>
                </div>

                <div className="flex shrink-0 items-center gap-4">
                  <div className="text-right">
                    <p className="flex items-center justify-end gap-1.5 font-display text-2xl text-accent tnum">
                      <Users size={18} className="text-muted" />
                      {exam.taken_count}
                      <span className="text-base text-muted">/ {exam.student_count}</span>
                    </p>
                    <p className="text-[0.66rem] uppercase tracking-[0.14em] text-muted">taken / students</p>
                  </div>
                  <CaretRight size={18} className="text-muted transition-colors group-hover:text-accent" />
                </div>
              </Link>

              {canManage && (
                <button
                  type="button"
                  onClick={() => void toggleArchive(exam)}
                  disabled={archiveBusyId === exam.id}
                  title={exam.archived ? "Unarchive exam" : "Archive (file away) this exam"}
                  className="mr-4 inline-flex shrink-0 items-center gap-1.5 rounded-full border border-line-strong px-3 py-2 text-[0.78rem] font-medium text-ink-soft transition-colors hover:border-accent/40 hover:text-accent disabled:opacity-45"
                >
                  {exam.archived ? <Restore size={14} /> : <Archive size={14} />}
                  {archiveBusyId === exam.id ? "…" : exam.archived ? "Unarchive" : "Archive"}
                </button>
              )}
            </Card>
          );
        })}
        {!exams.length && !refreshing && (
          <Card className="p-6">
            <p className="text-[0.85rem] text-muted">
              No exams to review yet{showArchived ? "" : " (try Show archived)"}.
            </p>
          </Card>
        )}
      </div>
    </div>
  );
}
