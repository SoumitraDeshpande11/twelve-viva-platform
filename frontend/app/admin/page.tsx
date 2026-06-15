"use client";

import { FormEvent, useEffect, useState } from "react";
import { CheckCircle2, FileArchive, RefreshCcw, Upload } from "lucide-react";
import { api, Exam } from "../../lib/api";
import { AuthPanel } from "../AuthPanel";

export default function AdminPage() {
  const [exams, setExams] = useState<Exam[]>([]);
  const [selected, setSelected] = useState<Exam | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [needsAuth, setNeedsAuth] = useState(false);

  async function loadExams() {
    const data = await api<Exam[]>("/api/admin/exams");
    setExams(data);
    if (data[0]) {
      setSelected(await api<Exam>(`/api/admin/exams/${data[0].id}`));
    }
  }

  useEffect(() => {
    api("/api/auth/me").then(() => loadExams()).catch(() => setNeedsAuth(true));
  }, []);

  async function createExam(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setMessage("");
    try {
      const form = new FormData(event.currentTarget);
      const exam = await api<Exam>("/api/admin/exams", { method: "POST", body: form });
      setSelected(exam);
      setExams((current) => [exam, ...current.filter((item) => item.id !== exam.id)]);
      event.currentTarget.reset();
      setMessage("Exam created. Copy the displayed one-time codes now; they will not be shown again after refresh.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Failed to create exam.");
    } finally {
      setLoading(false);
    }
  }

  if (needsAuth) {
    return <AuthPanel onReady={() => { setNeedsAuth(false); loadExams().catch((error) => setMessage(error.message)); }} />;
  }

  return (
    <main className="shell">
      <div className="page-head">
        <div>
          <h1>Admin Setup</h1>
          <p>Upload the exam context once. TWELVE creates server-side student records and indexes submitted materials for the viva agent.</p>
        </div>
        <button className="secondary" onClick={() => loadExams()} type="button">
          <RefreshCcw size={17} /> Refresh
        </button>
      </div>

      <section className="grid two">
        <form className="panel" onSubmit={createExam}>
          <h2 className="section-title">Create Exam</h2>
          <div className="field">
            <label htmlFor="name">Exam name</label>
            <input id="name" name="name" required placeholder="CSE Project Viva - June 2026" />
          </div>
          <div className="field">
            <label htmlFor="problem_statement">Problem statement</label>
            <textarea id="problem_statement" name="problem_statement" required placeholder="Paste the official problem statement or evaluation brief." />
          </div>
          <div className="field">
            <label htmlFor="curriculum">Curriculum/topics</label>
            <textarea id="curriculum" name="curriculum" required placeholder="DBMS, OS, software engineering, networks, AI, security..." />
          </div>
          <div className="field">
            <label htmlFor="rubric">Rubric</label>
            <textarea id="rubric" name="rubric" required placeholder="Correctness 30%, implementation depth 30%, explanation 25%, originality 15%." />
          </div>
          <div className="field">
            <label htmlFor="mark_mode">Mark mode</label>
            <select id="mark_mode" name="mark_mode" defaultValue="professor_approved">
              <option value="professor_approved">Professor approved</option>
              <option value="ai_official">AI official unless overridden</option>
            </select>
          </div>
          <div className="grid two">
            <div className="field">
              <label htmlFor="starts_at">Opens at (optional)</label>
              <input id="starts_at" name="starts_at" type="datetime-local" />
            </div>
            <div className="field">
              <label htmlFor="ends_at">Closes at (optional)</label>
              <input id="ends_at" name="ends_at" type="datetime-local" />
            </div>
          </div>
          <div className="field">
            <label htmlFor="student_csv">Student CSV</label>
            <input id="student_csv" name="student_csv" type="file" accept=".csv" required />
          </div>
          <div className="field">
            <label htmlFor="submissions">Project submissions</label>
            <input id="submissions" name="submissions" type="file" multiple accept=".pdf,.docx,.zip,.txt,.md,.csv,.json" />
          </div>
          <button className="primary" disabled={loading} type="submit">
            <Upload size={17} /> {loading ? "Uploading..." : "Create exam"}
          </button>
          {message && <p className="small muted">{message}</p>}
        </form>

        <aside className="panel">
          <h2 className="section-title">Created Exams</h2>
          <div className="grid">
            {exams.map((exam) => (
              <button key={exam.id} className={selected?.id === exam.id ? "secondary" : ""} onClick={async () => setSelected(await api<Exam>(`/api/admin/exams/${exam.id}`))} type="button">
                <span>{exam.name}</span>
                <span className="muted small">{exam.student_count ?? 0} students · {exam.session_count ?? 0} sessions</span>
                {(exam.starts_at || exam.ends_at) && (
                  <span className="muted small">
                    Window {exam.starts_at ? new Date(exam.starts_at).toLocaleString() : "any"} → {exam.ends_at ? new Date(exam.ends_at).toLocaleString() : "any"}
                  </span>
                )}
              </button>
            ))}
            {!exams.length && <p className="muted small">No exams yet.</p>}
          </div>
        </aside>
      </section>

      {selected && (
        <section className="panel" style={{ marginTop: 16 }}>
          <div className="row">
            <CheckCircle2 size={20} color="var(--ok)" />
            <h2 className="section-title" style={{ margin: 0 }}>{selected.name}</h2>
          </div>
          <div className="grid two" style={{ marginTop: 14 }}>
            <div>
              <h3 className="section-title">Students</h3>
              <div className="timeline">
                {(selected.students ?? []).map((student) => (
                  <div className="timeline-item" key={student.id}>
                    <strong>{student.roll_number}</strong> · {student.name}
                    <div className="muted small">Login token: {student.token}</div>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <h3 className="section-title">Submissions</h3>
              <div className="timeline">
                {(selected.submissions ?? []).map((submission) => (
                  <div className="timeline-item" key={submission.id}>
                    <FileArchive size={16} /> {submission.filename}
                    <div className="muted small">{submission.mime_type || "unknown type"}</div>
                  </div>
                ))}
                {!selected.submissions?.length && <p className="muted small">No submissions uploaded.</p>}
              </div>
            </div>
          </div>
        </section>
      )}
    </main>
  );
}
