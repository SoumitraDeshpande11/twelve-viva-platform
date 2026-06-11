import Link from "next/link";
import { FileUp, GraduationCap, Mic2, ShieldAlert } from "lucide-react";

export default function Home() {
  return (
    <main className="shell">
      <div className="page-head">
        <div>
          <h1>TWELVE AI Viva Pilot</h1>
          <p>
            A browser-based viva flow for exam setup, AI-led questioning, typed or voice answers, proctoring flags, and professor review transcripts.
          </p>
        </div>
      </div>
      <section className="grid three">
        <Link className="card" href="/admin">
          <FileUp size={28} />
          <h2 className="section-title">Admin Setup</h2>
          <p className="muted small">Create exams, upload student CSVs, rubrics, problem statements, and submissions.</p>
        </Link>
        <Link className="card" href="/student">
          <Mic2 size={28} />
          <h2 className="section-title">Student Viva</h2>
          <p className="muted small">Run the viva with camera, mic, fullscreen checks, spoken questions, and typed fallback.</p>
        </Link>
        <Link className="card" href="/review">
          <ShieldAlert size={28} />
          <h2 className="section-title">Professor Review</h2>
          <p className="muted small">Inspect scores, reasoning, transcripts, audio references, and proctoring timelines.</p>
        </Link>
      </section>
      <section className="panel" style={{ marginTop: 18 }}>
        <div className="row">
          <GraduationCap size={20} />
          <strong>Browser-only pilot boundary</strong>
        </div>
        <p className="muted small">
          TWELVE logs fullscreen exit, tab switching, focus loss, camera/mic loss, and screen-share interruptions. A normal browser cannot enforce OS-level kiosk lockdown.
        </p>
      </section>
    </main>
  );
}
