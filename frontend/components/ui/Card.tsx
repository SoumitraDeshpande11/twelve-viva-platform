import { cn } from "../../lib/cn";

export function Card({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "relative rounded-[var(--radius-card)] border border-line bg-surface shadow-[var(--shadow-card)]",
        className
      )}
      {...props}
    >
      {children}
    </div>
  );
}

/** Section header with a ledger marker (№ 01) — the editorial signature. */
export function SectionTitle({
  marker,
  title,
  hint,
  className,
  action,
}: {
  marker?: string;
  title: string;
  hint?: string;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-start justify-between gap-4", className)}>
      <div className="flex items-baseline gap-3">
        {marker && (
          <span className="font-mono text-[0.7rem] tracking-widest text-accent tnum mt-1">
            {marker}
          </span>
        )}
        <div>
          <h2 className="text-lg text-ink">{title}</h2>
          {hint && <p className="mt-0.5 text-[0.8rem] text-muted">{hint}</p>}
        </div>
      </div>
      {action}
    </div>
  );
}
