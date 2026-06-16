"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  FileText,
  GraduationCap,
  Scroll as ScrollText,
  SignOut,
  Users,
} from "@phosphor-icons/react";
import { cn } from "../lib/cn";
import { getMe, logout, type Me, type StaffRole } from "../lib/api";
import { ThemeToggle } from "./ui/ThemeToggle";
import { Button } from "./ui/Button";

/**
 * Nav links gated by staff role. `roles: null` = always visible (e.g. the
 * student viva surface, which is not a staff route). Role sets mirror the
 * backend `require_staff(...)` checks for each surface.
 */
const NAV: {
  href: string;
  label: string;
  Icon: typeof Users;
  roles: StaffRole[] | null;
  studentOnly?: boolean;
}[] = [
  { href: "/admin", label: "Admin", Icon: Users, roles: ["super_admin", "exam_admin"] },
  // Viva is a student surface; staff must not sit a viva (backend rejects it too),
  // so hide it once a staff member is logged in.
  { href: "/student", label: "Viva", Icon: ScrollText, roles: null, studentOnly: true },
  {
    href: "/review",
    label: "Review",
    Icon: FileText,
    roles: ["super_admin", "exam_admin", "examiner"],
  },
];

const ROLE_LABELS: Record<StaffRole, string> = {
  super_admin: "Super admin",
  exam_admin: "Exam admin",
  examiner: "Examiner",
  invigilator: "Invigilator",
};
const ROLE_ORDER: StaffRole[] = ["super_admin", "exam_admin", "examiner", "invigilator"];

/** Compact role badge: highest-privilege role + "+N" when the user holds several. */
function primaryRoleLabel(roles: StaffRole[]): string {
  const top = ROLE_ORDER.find((role) => roles.includes(role));
  if (!top) return "Staff";
  return roles.length > 1 ? `${ROLE_LABELS[top]} +${roles.length - 1}` : ROLE_LABELS[top];
}

function navIsVisible(item: (typeof NAV)[number], me: Me | null): boolean {
  // Student-only links are hidden from logged-in staff (conflict of interest).
  if (item.studentOnly) return me?.role !== "staff";
  // Always-visible links.
  if (item.roles === null) return true;
  // Degrade gracefully when identity is unknown (not logged in / failed fetch):
  // show staff links so existing flows still work and the API enforces 403.
  if (!me || me.role !== "staff") return true;
  return item.roles.some((role) => me.roles.includes(role));
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [me, setMe] = useState<Me | null>(null);
  const [loggingOut, setLoggingOut] = useState(false);
  // Student "End session" is consequential mid-viva, so it routes through a confirm step.
  const [confirmEnd, setConfirmEnd] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getMe()
      .then((data) => {
        if (!cancelled) setMe(data);
      })
      .catch(() => {
        if (!cancelled) setMe(null);
      });
    return () => {
      cancelled = true;
    };
  }, [pathname]);

  const staff = me?.role === "staff" ? me : null;
  const student = me?.role === "student" ? me : null;
  const displayName = staff?.user.name || staff?.user.email || null;

  // `target` is where to land after the session is cleared: staff back to home,
  // student (and the staff "test as a student" path) to the /student entry form.
  async function handleLogout(target = "/") {
    setLoggingOut(true);
    try {
      await logout();
    } catch {
      // Ignore — still redirect so the user is not stuck on a stale session.
    } finally {
      window.location.assign(target);
    }
  }

  return (
    <div className="relative z-10 flex min-h-dvh flex-col">
      <header className="sticky top-0 z-40 border-b border-line bg-paper/85 backdrop-blur-md">
        <div className="mx-auto flex h-16 w-full max-w-6xl items-center justify-between px-5">
          <Link href="/" className="group flex items-center gap-2.5" aria-label="TWELVE home">
            <span className="flex h-8 w-8 items-center justify-center rounded-md border border-accent/40 bg-accent-soft text-accent">
              <GraduationCap size={18} />
            </span>
            <span className="flex flex-col leading-none">
              <span className="font-display text-[1.05rem] tracking-tight text-ink">TWELVE</span>
              <span className="text-[0.6rem] uppercase tracking-[0.24em] text-muted">
                viva pilot
              </span>
            </span>
          </Link>

          <div className="flex items-center gap-1.5">
            <nav aria-label="Primary" className="mr-2 flex items-center gap-0.5">
              {NAV.filter((item) => navIsVisible(item, me)).map(({ href, label, Icon }) => {
                const active = pathname === href || pathname.startsWith(`${href}/`);
                return (
                  <Link
                    key={href}
                    href={href}
                    className={cn(
                      "flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[0.82rem] font-medium tracking-tight transition-colors",
                      active
                        ? "bg-accent-soft text-accent"
                        : "text-ink-soft hover:bg-surface-2 hover:text-ink"
                    )}
                  >
                    <Icon size={15} />
                    <span className="hidden sm:inline">{label}</span>
                  </Link>
                );
              })}
            </nav>
            {staff && (
              <div className="mr-1 flex items-center gap-1.5">
                <span className="hidden flex-col items-end leading-none sm:flex">
                  <span className="text-[0.78rem] font-medium tracking-tight text-ink">
                    {displayName}
                  </span>
                  <span
                    className="text-[0.6rem] uppercase tracking-[0.18em] text-muted"
                    title={staff.roles.join(", ").replace(/_/g, " ")}
                  >
                    {primaryRoleLabel(staff.roles)}
                  </span>
                </span>
                {/* Staff cannot sit a viva while signed in (backend rejects it, and the
                    Viva nav is hidden). Signpost the path: log out → /student entry. */}
                <button
                  type="button"
                  onClick={() => handleLogout("/student")}
                  disabled={loggingOut}
                  title="Log out and open the student entry form to test the viva"
                  className="hidden h-9 items-center gap-1.5 rounded-full border border-line-strong bg-surface px-3 text-[0.78rem] font-medium text-ink-soft transition-colors hover:border-accent/40 hover:text-accent disabled:opacity-45 sm:inline-flex"
                >
                  <GraduationCap size={15} /> Test as student
                </button>
                <button
                  type="button"
                  onClick={() => handleLogout("/")}
                  disabled={loggingOut}
                  aria-label="Log out"
                  className="inline-flex h-9 w-9 items-center justify-center rounded-full border border-line-strong bg-surface text-ink-soft transition-colors hover:border-accent/40 hover:text-accent disabled:opacity-45"
                >
                  <SignOut size={16} />
                </button>
              </div>
            )}
            {student && (
              <div className="relative mr-1 flex items-center gap-1.5">
                <span className="hidden items-center gap-1.5 rounded-full border border-accent/30 bg-accent-soft px-2.5 py-1 text-[0.72rem] font-medium tracking-tight text-accent sm:inline-flex">
                  <span className="h-1.5 w-1.5 rounded-full bg-accent" aria-hidden />
                  Student attempt
                </span>
                <button
                  type="button"
                  onClick={() => setConfirmEnd(true)}
                  disabled={loggingOut}
                  className="inline-flex h-9 items-center gap-1.5 rounded-full border border-danger/40 bg-surface px-3 text-[0.78rem] font-medium text-danger transition-colors hover:bg-danger-soft disabled:opacity-45"
                >
                  <SignOut size={15} /> End session
                </button>
                {confirmEnd && (
                  <>
                    {/* Click-away backdrop. */}
                    <div
                      className="fixed inset-0 z-40"
                      aria-hidden
                      onClick={() => setConfirmEnd(false)}
                    />
                    <div
                      role="dialog"
                      aria-modal="true"
                      aria-label="End your viva session"
                      className="absolute right-0 top-12 z-50 w-72 rounded-[var(--radius-control)] border border-line-strong bg-paper p-4 shadow-[var(--shadow-card)]"
                    >
                      <p className="text-[0.85rem] font-medium text-ink">End your viva session?</p>
                      <p className="mt-1.5 text-[0.78rem] leading-snug text-muted">
                        Your progress is saved. You&apos;ll need a valid one-time code to resume — if
                        yours is already used, ask an invigilator to reset your attempt.
                      </p>
                      <div className="mt-3 flex justify-end gap-2">
                        <Button size="sm" variant="ghost" onClick={() => setConfirmEnd(false)}>
                          Cancel
                        </Button>
                        <Button
                          size="sm"
                          variant="danger"
                          disabled={loggingOut}
                          onClick={() => handleLogout("/student")}
                        >
                          End session
                        </Button>
                      </div>
                    </div>
                  </>
                )}
              </div>
            )}
            <ThemeToggle />
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-5 py-8 md:py-12">{children}</main>

      <footer className="border-t border-line">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-1 px-5 py-6 text-[0.72rem] text-muted sm:flex-row sm:items-center sm:justify-between">
          <span>TWELVE — browser-based AI viva pilot.</span>
          <span className="font-mono tracking-tight">
            Proctoring signals are review flags only — never scored.
          </span>
        </div>
      </footer>
    </div>
  );
}

/** Page heading block used at the top of each route. */
export function PageHeading({
  eyebrow,
  title,
  children,
  action,
}: {
  eyebrow: string;
  title: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="reveal mb-8 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="max-w-2xl">
        <p className="mb-2 font-mono text-[0.7rem] uppercase tracking-[0.22em] text-accent">
          {eyebrow}
        </p>
        <h1 className="text-3xl text-ink md:text-[2.4rem] md:leading-[1.05]">{title}</h1>
        {children && <p className="mt-3 text-[0.95rem] leading-relaxed text-muted">{children}</p>}
      </div>
      {action}
    </div>
  );
}
