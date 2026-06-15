"use client";

import Link from "next/link";
import { ArrowUpRight, ClipboardText as ClipboardList, Microphone as Mic2, Scroll as ScrollText, ShieldCheck } from "@phosphor-icons/react";
import { PageHeading } from "../components/AppShell";
import { Card } from "../components/ui/Card";

const ROLES = [
  {
    href: "/admin",
    marker: "№ 01",
    Icon: ClipboardList,
    title: "Admin Setup",
    body: "Compose the exam once — student roster, problem statement, curriculum, rubric, and submitted work. One-time codes are issued at creation.",
  },
  {
    href: "/student",
    marker: "№ 02",
    Icon: Mic2,
    title: "Student Viva",
    body: "A proctored oral exam in the browser. The AI asks, the student answers by voice or text, and the question stays on screen exactly as posed.",
  },
  {
    href: "/review",
    marker: "№ 03",
    Icon: ScrollText,
    title: "Professor Review",
    body: "Read the full transcript, per-answer scoring and reasoning, the proctoring timeline, and record an override without disturbing the AI score.",
  },
];

export default function Home() {
  return (
    <div>
      <PageHeading eyebrow="The examiner's ledger" title="An oral examination, conducted in the browser.">
        TWELVE runs exam setup, AI-led questioning, voice or typed answers, browser proctoring
        flags, and professor review — each kept as a calm, auditable record.
      </PageHeading>

      <section className="grid gap-4 md:grid-cols-3">
        {ROLES.map(({ href, marker, Icon, title, body }, index) => (
          <Link key={href} href={href} className="reveal group" style={{ animationDelay: `${index * 90}ms` }}>
            <Card className="flex h-full flex-col gap-4 p-6 transition-[transform,border-color,box-shadow] duration-200 group-hover:-translate-y-1 group-hover:border-accent/40 group-hover:shadow-[var(--shadow-float)]">
              <div className="flex items-center justify-between">
                <span className="flex h-11 w-11 items-center justify-center rounded-lg border border-line bg-surface-2 text-accent">
                  <Icon size={20} />
                </span>
                <span className="font-mono text-[0.7rem] tracking-[0.2em] text-muted">{marker}</span>
              </div>
              <div className="flex-1">
                <h2 className="flex items-center gap-1.5 text-xl text-ink">
                  {title}
                  <ArrowUpRight size={17} className="text-muted transition-transform duration-200 group-hover:translate-x-0.5 group-hover:-translate-y-0.5 group-hover:text-accent" />
                </h2>
                <p className="mt-2 text-[0.85rem] leading-relaxed text-muted">{body}</p>
              </div>
            </Card>
          </Link>
        ))}
      </section>

      <Card className="reveal mt-5 flex flex-col gap-3 p-6 sm:flex-row sm:items-start" style={{ animationDelay: "300ms" }}>
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-warn/30 bg-warn-soft text-warn">
          <ShieldCheck size={17} />
        </span>
        <div>
          <h3 className="text-base text-ink">A browser-only boundary, stated plainly.</h3>
          <p className="mt-1.5 max-w-3xl text-[0.83rem] leading-relaxed text-muted">
            TWELVE logs fullscreen exit, tab switching, focus loss, camera or microphone loss, and
            screen-share interruptions. A normal browser cannot enforce OS-level kiosk lockdown —
            proctoring signals are stored for review and never enter the score.
          </p>
        </div>
      </Card>
    </div>
  );
}
