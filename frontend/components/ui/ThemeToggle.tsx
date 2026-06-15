"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "@phosphor-icons/react";
import { useTheme } from "next-themes";
import { cn } from "../../lib/cn";

export function ThemeToggle({ className }: { className?: string }) {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const isDark = resolvedTheme === "dark";

  return (
    <button
      type="button"
      aria-label="Toggle light or dark theme"
      onClick={() => setTheme(isDark ? "light" : "dark")}
      className={cn(
        "inline-flex h-9 w-9 items-center justify-center rounded-full border border-line-strong bg-surface text-ink-soft transition-colors hover:text-accent hover:border-accent/40",
        className
      )}
    >
      {/* Render a stable icon until mounted to avoid hydration mismatch. */}
      {mounted ? (
        isDark ? <Sun size={16} /> : <Moon size={16} />
      ) : (
        <Sun size={16} className="opacity-0" />
      )}
    </button>
  );
}
