import { cn } from "../../lib/cn";

/** Ruled-ledger list: a hairline left rail with marker dots. */
export function Timeline({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <ol className={cn("relative ml-1.5 flex flex-col gap-3 border-l border-line pl-5", className)}>
      {children}
    </ol>
  );
}

type Tone = "neutral" | "ok" | "warn" | "danger" | "accent";

const dotTones: Record<Tone, string> = {
  neutral: "bg-line-strong",
  ok: "bg-ok",
  warn: "bg-warn",
  danger: "bg-danger",
  accent: "bg-accent",
};

export function TimelineItem({
  tone = "neutral",
  children,
  className,
}: {
  tone?: Tone;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <li className={cn("relative", className)}>
      <span
        className={cn(
          "absolute -left-[1.4rem] top-1.5 h-2.5 w-2.5 rounded-full ring-4 ring-surface",
          dotTones[tone]
        )}
      />
      <div className="rounded-[var(--radius-control)] border border-line bg-surface-2/40 px-3.5 py-2.5">
        {children}
      </div>
    </li>
  );
}
