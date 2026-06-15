import { forwardRef } from "react";
import { cn } from "../../lib/cn";

export function Field({
  label,
  htmlFor,
  hint,
  children,
  className,
}: {
  label: string;
  htmlFor?: string;
  hint?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mb-4 flex flex-col gap-1.5", className)}>
      <label
        htmlFor={htmlFor}
        className="text-[0.72rem] font-medium uppercase tracking-[0.14em] text-muted"
      >
        {label}
      </label>
      {children}
      {hint && <p className="text-[0.75rem] text-muted">{hint}</p>}
    </div>
  );
}

const controlBase =
  "w-full rounded-[var(--radius-control)] border border-line-strong bg-paper/60 px-3 text-[0.9rem] text-ink " +
  "placeholder:text-muted/70 transition-colors duration-150 " +
  "hover:border-accent/40 focus:border-accent focus-visible:outline-none";

export const Input = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input ref={ref} className={cn(controlBase, "h-10", className)} {...props} />
  )
);
Input.displayName = "Input";

export const Textarea = forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea ref={ref} className={cn(controlBase, "min-h-[96px] resize-y py-2.5 leading-relaxed", className)} {...props} />
));
Textarea.displayName = "Textarea";
