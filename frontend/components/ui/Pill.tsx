import { cn } from "../../lib/cn";

type Tone = "neutral" | "ok" | "warn" | "danger" | "accent";

const tones: Record<Tone, string> = {
  neutral: "border-line-strong text-ink-soft bg-surface-2",
  ok: "border-ok/30 text-ok bg-ok-soft",
  warn: "border-warn/30 text-warn bg-warn-soft",
  danger: "border-danger/30 text-danger bg-danger-soft",
  accent: "border-accent/30 text-accent bg-accent-soft",
};

export function StatusPill({
  tone = "neutral",
  children,
  className,
  pulse,
}: {
  tone?: Tone;
  children: React.ReactNode;
  className?: string;
  pulse?: boolean;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[0.72rem] font-medium tracking-tight",
        tones[tone],
        pulse && "pulse",
        className
      )}
    >
      {children}
    </span>
  );
}

/** Circular academic seal for a percentage score. */
export function ScorePill({
  value,
  label,
  tone = "accent",
  className,
}: {
  value: number | null | undefined;
  label?: string;
  tone?: Tone;
  className?: string;
}) {
  const ringTone: Record<Tone, string> = {
    neutral: "border-line-strong text-ink",
    ok: "border-ok/50 text-ok",
    warn: "border-warn/50 text-warn",
    danger: "border-danger/50 text-danger",
    accent: "border-accent/50 text-accent",
  };
  return (
    <div className={cn("flex flex-col items-center gap-1", className)}>
      <div
        className={cn(
          "flex h-16 w-16 flex-col items-center justify-center rounded-full border-2 bg-surface",
          ringTone[tone]
        )}
      >
        <span className="font-display text-xl leading-none tnum">
          {value ?? "—"}
        </span>
        <span className="text-[0.6rem] text-muted">{value != null ? "percent" : "pending"}</span>
      </div>
      {label && (
        <span className="text-[0.68rem] uppercase tracking-[0.12em] text-muted">{label}</span>
      )}
    </div>
  );
}
