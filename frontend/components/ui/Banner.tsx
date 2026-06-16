import { Info, Warning as AlertTriangle, CheckCircle as CheckCircle2, XCircle } from "@phosphor-icons/react";
import { cn } from "../../lib/cn";

type Tone = "info" | "ok" | "warn" | "danger";

const config: Record<Tone, { cls: string; Icon: typeof Info }> = {
  info: { cls: "border-line-strong bg-surface-2 text-ink-soft", Icon: Info },
  ok: { cls: "border-ok/30 bg-ok-soft text-ok", Icon: CheckCircle2 },
  warn: { cls: "border-warn/30 bg-warn-soft text-warn", Icon: AlertTriangle },
  danger: { cls: "border-danger/30 bg-danger-soft text-danger", Icon: XCircle },
};

export function Banner({
  tone = "info",
  children,
  className,
}: {
  tone?: Tone;
  children: React.ReactNode;
  className?: string;
}) {
  const { cls, Icon } = config[tone];
  // Errors must be announced immediately; info/success can be polite.
  const isUrgent = tone === "danger";
  return (
    <div
      role={isUrgent ? "alert" : "status"}
      aria-live={isUrgent ? "assertive" : "polite"}
      className={cn(
        "flex items-start gap-2.5 rounded-[var(--radius-control)] border px-3.5 py-2.5 text-[0.82rem] leading-snug",
        cls,
        className
      )}
    >
      <Icon size={16} className="mt-0.5 shrink-0" />
      <div className="min-w-0">{children}</div>
    </div>
  );
}
