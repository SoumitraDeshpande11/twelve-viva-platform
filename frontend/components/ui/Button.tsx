"use client";

import { forwardRef } from "react";
import { Slot } from "@radix-ui/react-slot";
import { cn } from "../../lib/cn";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "ok";
type Size = "sm" | "md";

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  asChild?: boolean;
}

const base =
  "inline-flex items-center justify-center gap-2 font-medium tracking-tight whitespace-nowrap rounded-[var(--radius-control)] " +
  "transition-[background-color,border-color,color,box-shadow,transform] duration-150 ease-out " +
  "disabled:opacity-45 disabled:pointer-events-none active:translate-y-px select-none";

const sizes: Record<Size, string> = {
  sm: "h-8 px-3 text-[0.8rem]",
  md: "h-10 px-4 text-[0.875rem]",
};

const variants: Record<Variant, string> = {
  primary:
    "bg-accent text-accent-ink border border-accent shadow-[var(--shadow-card)] hover:brightness-110",
  secondary:
    "bg-surface text-ink border border-line-strong hover:bg-surface-2",
  ghost:
    "bg-transparent text-ink-soft border border-transparent hover:bg-surface-2 hover:text-ink",
  danger:
    "bg-transparent text-danger border border-danger/40 hover:bg-danger-soft",
  ok: "bg-transparent text-ok border border-ok/40 hover:bg-ok-soft",
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "secondary", size = "md", asChild, type, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        ref={ref}
        className={cn(base, sizes[size], variants[variant], className)}
        {...(asChild ? {} : { type: type ?? "button" })}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";
