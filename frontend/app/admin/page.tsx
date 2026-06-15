"use client";

import { FormEvent, useEffect, useState } from "react";
import { CheckCircle as CheckCircle2, Archive as FileArchive, Key as KeyRound, ArrowsClockwise as RefreshCcw, UploadSimple as Upload, Users, Trash as Trash2, Copy, DownloadSimple as Download } from "@phosphor-icons/react";
import { api, createStaff, getMe, Exam, Student, StaffRole } from "../../lib/api";
import { AuthPanel } from "../AuthPanel";
import { PageHeading } from "../../components/AppShell";
import { Card, SectionTitle } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Field, Input, Textarea } from "../../components/ui/Field";
import { Select } from "../../components/ui/Select";
import { FileDrop } from "../../components/ui/FileDrop";
import { Banner } from "../../components/ui/Banner";
import { StatusPill } from "../../components/ui/Pill";

// create_exam may now return CSV parse diagnostics alongside the exam.
type CreatedExam = Exam & { skipped_rows?: number; warnings?: string[] };
// reset-attempt returns a freshly minted one-time code in plaintext.
type ResetAttemptResult = { student_id: string; roll_number: string; token: string };

// Selectable staff roles for the invite card, in display order.
const STAFF_ROLES: { value: StaffRole; label: string }[] = [
  { value: "super_admin", label: "Super admin" },
  { value: "exam_admin", label: "Exam admin" },
  { value: "examiner", label: "Examiner" },
  { value: "invigilator", label: "Invigilator" },
];

export default function AdminPage() {
  const [exams, setExams] = useState<Exam[]>([]);
  const [selected, setSelected] = useState<Exam | null>(null);
  const [markMode, setMarkMode] = useState("professor_approved");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [isError, setIsError] = useState(false);
  const [needsAuth, setNeedsAuth] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  // Created tokens are shown once and redacted on any later read; keep them in a
  // separate map (student_id -> token) so a Refresh doesn't make them vanish.
  const [createdTokens, setCreatedTokens] = useState<Record<string, string>>({});
  const [csvDiagnostics, setCsvDiagnostics] = useState<{ skipped: number; warnings: string[] } | null>(null);
  const [resettingId, setResettingId] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  // Current staff identity — drives whether the "Invite staff member" card shows.
  const [isSuperAdmin, setIsSuperAdmin] = useState(false);
  // Invite staff member form state.
  const [staffName, setStaffName] = useState("");
  const [staffEmail, setStaffEmail] = useState("");
  const [staffPassword, setStaffPassword] = useState("");
  const [staffRoles, setStaffRoles] = useState<StaffRole[]>(["examiner"]);
  const [staffBusy, setStaffBusy] = useState(false);
  const [staffMessage, setStaffMessage] = useState("");
  const [staffError, setStaffError] = useState(false);

  async function loadExams() {
    const data = await api<Exam[]>("/api/admin/exams");
    setExams(data);
    if (data[0]) {
      setSelected(await api<Exam>(`/api/admin/exams/${data[0].id}`));
    } else {
      setSelected(null);
    }
  }

  async function refreshExams() {
    setRefreshing(true);
    setLoadError("");
    try {
      await loadExams();
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "Failed to load exams.");
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    // Separate auth failure (show login) from data-load failure (stay, show banner).
    getMe()
      .then((identity) => {
        setIsSuperAdmin(identity.role === "staff" && identity.roles.includes("super_admin"));
        return loadExams().catch((error) =>
          setLoadError(error instanceof Error ? error.message : "Failed to load exams.")
        );
      })
      .catch(() => setNeedsAuth(true));
  }, []);

  async function createExam(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formEl = event.currentTarget;
    setLoading(true);
    setMessage("");
    setIsError(false);
    setCsvDiagnostics(null);
    try {
      const form = new FormData(formEl);
      form.set("mark_mode", markMode);
      // datetime-local is naive local time; send an offset-aware ISO so the backend
      // does not reinterpret the admin's local time as UTC.
      for (const fieldName of ["starts_at", "ends_at"]) {
        const value = form.get(fieldName);
        if (typeof value === "string" && value) {
          form.set(fieldName, new Date(value).toISOString());
        }
      }
      const exam = await api<CreatedExam>("/api/admin/exams", { method: "POST", body: form });
      setSelected(exam);
      setExams((current) => [exam, ...current.filter((item) => item.id !== exam.id)]);
      // Preserve the plaintext codes so a later Refresh (which redacts them) does not lose them.
      const tokens: Record<string, string> = {};
      for (const student of exam.students ?? []) {
        if (student.token) {
          tokens[student.id] = student.token;
        }
      }
      setCreatedTokens(tokens);
      if (typeof exam.skipped_rows === "number" || (exam.warnings && exam.warnings.length)) {
        setCsvDiagnostics({ skipped: exam.skipped_rows ?? 0, warnings: exam.warnings ?? [] });
      }
      formEl.reset();
      setMarkMode("professor_approved");
      setMessage("Exam created. Copy the one-time codes below now — they are redacted after refresh.");
    } catch (error) {
      setIsError(true);
      setMessage(error instanceof Error ? error.message : "Failed to create exam.");
    } finally {
      setLoading(false);
    }
  }

  async function deleteExam(exam: Exam) {
    if (!window.confirm(`Delete "${exam.name}"? This removes the exam and all its data. This cannot be undone.`)) {
      return;
    }
    setDeletingId(exam.id);
    setLoadError("");
    try {
      try {
        await api(`/api/admin/exams/${exam.id}`, { method: "DELETE" });
      } catch (error) {
        // A 204 has an empty body; api() then throws a JSON parse error. Treat that as success.
        if (!(error instanceof SyntaxError)) {
          throw error;
        }
      }
      setExams((current) => current.filter((item) => item.id !== exam.id));
      setSelected((current) => (current?.id === exam.id ? null : current));
      if (selected?.id === exam.id) {
        setCreatedTokens({});
      }
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "Failed to delete exam.");
    } finally {
      setDeletingId(null);
    }
  }

  async function resetAttempt(student: Student) {
    setResettingId(student.id);
    setLoadError("");
    try {
      const result = await api<ResetAttemptResult>(
        `/api/admin/exams/${selected?.id}/students/${student.id}/reset-attempt`,
        { method: "POST" }
      );
      setCreatedTokens((current) => ({ ...current, [result.student_id]: result.token }));
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : "Failed to reset attempt.");
    } finally {
      setResettingId(null);
    }
  }

  function codeRows(): { roll: string; token: string }[] {
    if (!selected) return [];
    return (selected.students ?? [])
      .map((student) => ({
        roll: student.roll_number,
        token: createdTokens[student.id] ?? student.token ?? "",
      }))
      .filter((row) => row.token);
  }

  async function copyAllCodes() {
    const rows = codeRows();
    if (!rows.length) return;
    const text = ["roll_number,token", ...rows.map((row) => `${row.roll},${row.token}`)].join("\n");
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setLoadError("Clipboard copy failed — use Download codes CSV instead.");
    }
  }

  function downloadCodesCsv() {
    const rows = codeRows();
    if (!rows.length) return;
    const text = ["roll_number,token", ...rows.map((row) => `${row.roll},${row.token}`)].join("\n");
    const blob = new Blob([text], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${selected?.name ?? "exam"}-codes.csv`.replace(/[^a-z0-9.-]+/gi, "_");
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  function toggleStaffRole(role: StaffRole) {
    setStaffRoles((current) =>
      current.includes(role) ? current.filter((item) => item !== role) : [...current, role]
    );
  }

  async function inviteStaff(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStaffBusy(true);
    setStaffMessage("");
    setStaffError(false);
    try {
      const created = await createStaff({
        name: staffName,
        email: staffEmail,
        password: staffPassword,
        roles: staffRoles,
      });
      const roleLabels = created.roles
        .map((role) => STAFF_ROLES.find((item) => item.value === role)?.label ?? role)
        .join(", ");
      setStaffMessage(`Staff member ${created.email} created with roles ${roleLabels}.`);
      setStaffName("");
      setStaffEmail("");
      setStaffPassword("");
      setStaffRoles(["examiner"]);
    } catch (error) {
      setStaffError(true);
      setStaffMessage(error instanceof Error ? error.message : "Failed to create staff member.");
    } finally {
      setStaffBusy(false);
    }
  }

  if (needsAuth) {
    return (
      <AuthPanel
        onReady={() => {
          setNeedsAuth(false);
          getMe()
            .then((identity) =>
              setIsSuperAdmin(identity.role === "staff" && identity.roles.includes("super_admin"))
            )
            .catch(() => undefined);
          loadExams().catch((error) =>
            setLoadError(error instanceof Error ? error.message : "Failed to load exams.")
          );
        }}
      />
    );
  }

  return (
    <div>
      <PageHeading
        eyebrow="№ 01 · Admin Setup"
        title="Compose the examination."
        action={
          <Button
            disabled={refreshing}
            onClick={() => {
              if (
                Object.keys(createdTokens).length &&
                !window.confirm(
                  "Refreshing reloads exams from the server, which redacts the one-time codes shown below. Copy or download them first. Continue?"
                )
              ) {
                return;
              }
              setCreatedTokens({});
              refreshExams();
            }}
          >
            <RefreshCcw size={16} /> {refreshing ? "Refreshing…" : "Refresh"}
          </Button>
        }
      >
        Upload the exam context once. TWELVE creates server-side student records and indexes the
        submitted materials for the viva agent.
      </PageHeading>

      {loadError && (
        <div className="mb-4">
          <Banner tone="danger">
            <div className="flex items-center justify-between gap-3">
              <span>{loadError}</span>
              <Button size="sm" variant="secondary" disabled={refreshing} onClick={() => refreshExams()}>
                <RefreshCcw size={14} /> Retry
              </Button>
            </div>
          </Banner>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <Card className="reveal p-6">
          <SectionTitle marker="№ 01" title="Create Exam" />
          <form onSubmit={createExam} className="mt-6">
            <Field label="Exam name" htmlFor="name">
              <Input id="name" name="name" required placeholder="CSE Project Viva — June 2026" />
            </Field>
            <Field label="Problem statement" htmlFor="problem_statement">
              <Textarea id="problem_statement" name="problem_statement" required placeholder="Paste the official problem statement or evaluation brief." />
            </Field>
            <Field label="Curriculum / topics" htmlFor="curriculum">
              <Textarea id="curriculum" name="curriculum" required placeholder="DBMS, OS, software engineering, networks, AI, security…" />
            </Field>
            <Field label="Rubric" htmlFor="rubric">
              <Textarea id="rubric" name="rubric" required placeholder="Correctness 30%, implementation depth 30%, explanation 25%, originality 15%." />
            </Field>
            <Field label="Mark mode" htmlFor="mark_mode" hint="Professor-approved holds scores provisional until override; AI-official publishes on completion.">
              <Select
                id="mark_mode"
                value={markMode}
                onValueChange={setMarkMode}
                options={[
                  { value: "professor_approved", label: "Professor approved" },
                  { value: "ai_official", label: "AI official unless overridden" },
                ]}
              />
            </Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Opens at (optional)" htmlFor="starts_at">
                <Input id="starts_at" name="starts_at" type="datetime-local" />
              </Field>
              <Field label="Closes at (optional)" htmlFor="ends_at">
                <Input id="ends_at" name="ends_at" type="datetime-local" />
              </Field>
            </div>
            <Field label="Student CSV" hint="Headers: roll_number, name, email.">
              <FileDrop name="student_csv" accept=".csv" required hint="Choose the student roster CSV" />
            </Field>
            <Field label="Project submissions" hint="PDF, DOCX, ZIP, TXT, MD, CSV, JSON. Filenames containing a roll number auto-link.">
              <FileDrop
                name="submissions"
                accept=".pdf,.docx,.zip,.txt,.md,.csv,.json"
                multiple
                hint="Choose one or more submissions"
              />
            </Field>
            <Button variant="primary" type="submit" disabled={loading} className="w-full">
              <Upload size={16} /> {loading ? "Uploading…" : "Create exam"}
            </Button>
            {message && (
              <div className="mt-4">
                <Banner tone={isError ? "danger" : "ok"}>{message}</Banner>
              </div>
            )}
            {csvDiagnostics && (csvDiagnostics.skipped > 0 || csvDiagnostics.warnings.length > 0) && (
              <div className="mt-3">
                <Banner tone="warn">
                  <p className="font-medium">
                    {csvDiagnostics.skipped > 0
                      ? `${csvDiagnostics.skipped} CSV row${csvDiagnostics.skipped === 1 ? "" : "s"} skipped during parsing.`
                      : "CSV parsed with warnings."}
                  </p>
                  {csvDiagnostics.warnings.length > 0 && (
                    <ul className="mt-1 list-disc space-y-0.5 pl-4">
                      {csvDiagnostics.warnings.map((warning, index) => (
                        <li key={index}>{warning}</li>
                      ))}
                    </ul>
                  )}
                </Banner>
              </div>
            )}
          </form>
        </Card>

        <Card className="reveal h-fit p-6" style={{ animationDelay: "80ms" }}>
          <SectionTitle marker="№ 02" title="Created Exams" />
          <div className="mt-5 flex flex-col gap-2">
            {exams.map((exam) => (
              <div
                key={exam.id}
                className={`flex items-stretch gap-2 rounded-[var(--radius-control)] border transition-colors ${
                  selected?.id === exam.id
                    ? "border-accent/50 bg-accent-soft"
                    : "border-line bg-surface-2/40 hover:border-accent/30"
                }`}
              >
                <button
                  type="button"
                  onClick={async () => {
                    setLoadError("");
                    try {
                      setSelected(await api<Exam>(`/api/admin/exams/${exam.id}`));
                    } catch (error) {
                      setLoadError(error instanceof Error ? error.message : "Failed to load exam.");
                    }
                  }}
                  className="flex min-w-0 flex-1 flex-col gap-1 px-3.5 py-3 text-left"
                >
                  <span className="truncate text-[0.9rem] font-medium text-ink">{exam.name}</span>
                  <span className="text-[0.74rem] text-muted">
                    {exam.student_count ?? 0} students · {exam.session_count ?? 0} sessions
                  </span>
                  {(exam.starts_at || exam.ends_at) && (
                    <span className="text-[0.72rem] text-muted">
                      Window {exam.starts_at ? new Date(exam.starts_at).toLocaleString() : "any"} →{" "}
                      {exam.ends_at ? new Date(exam.ends_at).toLocaleString() : "any"}
                    </span>
                  )}
                </button>
                <button
                  type="button"
                  aria-label={`Delete ${exam.name}`}
                  disabled={deletingId === exam.id}
                  onClick={() => deleteExam(exam)}
                  className="flex shrink-0 items-center px-3 text-muted transition-colors hover:text-danger disabled:opacity-45 disabled:pointer-events-none"
                >
                  <Trash2 size={15} />
                </button>
              </div>
            ))}
            {!exams.length && <p className="text-[0.82rem] text-muted">No exams yet.</p>}
          </div>

          {isSuperAdmin && (
            <div className="mt-6 border-t border-line pt-6">
              <SectionTitle
                marker="№ 03"
                title="Invite staff member"
                hint="Create another staff account. Super-admin only."
              />
              <form onSubmit={inviteStaff} className="mt-5">
                <Field label="Name" htmlFor="staff_name">
                  <Input
                    id="staff_name"
                    name="staff_name"
                    required
                    value={staffName}
                    onChange={(event) => setStaffName(event.target.value)}
                    placeholder="Dr. Asha Rao"
                  />
                </Field>
                <Field label="Email" htmlFor="staff_email">
                  <Input
                    id="staff_email"
                    name="staff_email"
                    type="email"
                    required
                    value={staffEmail}
                    onChange={(event) => setStaffEmail(event.target.value)}
                    placeholder="asha.rao@example.edu"
                  />
                </Field>
                <Field
                  label="Password"
                  htmlFor="staff_password"
                  hint="At least 10 characters."
                >
                  <Input
                    id="staff_password"
                    name="staff_password"
                    type="password"
                    required
                    minLength={10}
                    value={staffPassword}
                    onChange={(event) => setStaffPassword(event.target.value)}
                    placeholder="••••••••••"
                  />
                </Field>
                <Field label="Roles" hint="Select at least one role.">
                  <div className="flex flex-col gap-2">
                    {STAFF_ROLES.map((role) => (
                      <label
                        key={role.value}
                        className="flex items-center gap-2.5 text-[0.86rem] text-ink"
                      >
                        <input
                          type="checkbox"
                          className="accent-accent"
                          checked={staffRoles.includes(role.value)}
                          onChange={() => toggleStaffRole(role.value)}
                        />
                        {role.label}
                      </label>
                    ))}
                  </div>
                </Field>
                <Button
                  variant="primary"
                  type="submit"
                  disabled={staffBusy || staffRoles.length === 0}
                  className="w-full"
                >
                  <Users size={16} /> {staffBusy ? "Creating…" : "Invite staff member"}
                </Button>
                {staffMessage && (
                  <div className="mt-4">
                    <Banner tone={staffError ? "danger" : "ok"}>{staffMessage}</Banner>
                  </div>
                )}
              </form>
            </div>
          )}
        </Card>
      </div>

      {selected && (
        <Card className="reveal mt-4 p-6">
          <div className="flex items-center gap-2">
            <CheckCircle2 size={19} className="text-ok" />
            <h2 className="text-xl text-ink">{selected.name}</h2>
            {selected.mark_mode && (
              <StatusPill tone="accent" className="ml-1">
                {selected.mark_mode === "ai_official" ? "AI official" : "Professor approved"}
              </StatusPill>
            )}
          </div>

          <div className="mt-6 grid gap-6 md:grid-cols-2">
            <div>
              <SectionTitle marker="№ 03" title="Students" hint="One-time login codes shown once." />
              {codeRows().length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button size="sm" variant="secondary" onClick={copyAllCodes}>
                    <Copy size={14} /> {copied ? "Copied" : "Copy all codes"}
                  </Button>
                  <Button size="sm" variant="secondary" onClick={downloadCodesCsv}>
                    <Download size={14} /> Download codes CSV
                  </Button>
                </div>
              )}
              <div className="mt-4 flex flex-col gap-2">
                {(selected.students ?? []).map((student) => {
                  const token = createdTokens[student.id] ?? student.token;
                  return (
                  <div
                    key={student.id}
                    className="flex items-center justify-between gap-3 rounded-[var(--radius-control)] border border-line bg-surface-2/40 px-3.5 py-2.5"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-[0.86rem] text-ink">
                        <span className="font-mono text-accent">{student.roll_number}</span> · {student.name}
                      </p>
                    </div>
                    <div className="flex shrink-0 items-center gap-2">
                      {token && (
                        <span className="flex items-center gap-1.5 rounded-full border border-accent/30 bg-accent-soft px-2.5 py-1 font-mono text-[0.74rem] text-accent">
                          <KeyRound size={12} /> {token}
                        </span>
                      )}
                      <button
                        type="button"
                        aria-label={`Reset attempt and regenerate code for ${student.roll_number}`}
                        title="Reset attempt & regenerate code"
                        disabled={resettingId === student.id}
                        onClick={() => resetAttempt(student)}
                        className="flex items-center text-muted transition-colors hover:text-accent disabled:opacity-45 disabled:pointer-events-none"
                      >
                        <RefreshCcw size={14} />
                      </button>
                    </div>
                  </div>
                  );
                })}
                {!selected.students?.length && (
                  <p className="flex items-center gap-2 text-[0.82rem] text-muted">
                    <Users size={15} /> No students parsed.
                  </p>
                )}
              </div>
            </div>

            <div>
              <SectionTitle marker="№ 04" title="Submissions" />
              <div className="mt-4 flex flex-col gap-2">
                {(selected.submissions ?? []).map((submission) => (
                  <div
                    key={submission.id}
                    className="flex items-center gap-2.5 rounded-[var(--radius-control)] border border-line bg-surface-2/40 px-3.5 py-2.5"
                  >
                    <FileArchive size={16} className="shrink-0 text-muted" />
                    <div className="min-w-0">
                      <p className="truncate text-[0.84rem] text-ink">{submission.filename}</p>
                      <p className="text-[0.72rem] text-muted">{submission.mime_type || "unknown type"}</p>
                    </div>
                  </div>
                ))}
                {!selected.submissions?.length && (
                  <p className="text-[0.82rem] text-muted">No submissions uploaded.</p>
                )}
              </div>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
