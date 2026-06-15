"use client";

import { useRef, useState } from "react";
import { CloudArrowUp as UploadCloud } from "@phosphor-icons/react";
import { cn } from "../../lib/cn";

/** Styled file input that reports chosen filenames. Wraps a real <input> so it
    submits normally inside a FormData form. */
export function FileDrop({
  name,
  accept,
  multiple,
  required,
  hint,
}: {
  name: string;
  accept?: string;
  multiple?: boolean;
  required?: boolean;
  hint?: string;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [names, setNames] = useState<string[]>([]);

  return (
    <div
      onClick={() => inputRef.current?.click()}
      className={cn(
        "group flex cursor-pointer flex-col items-center justify-center gap-2 rounded-[var(--radius-control)]",
        "border border-dashed border-line-strong bg-paper/40 px-4 py-6 text-center transition-colors",
        "hover:border-accent/50 hover:bg-accent-soft/40"
      )}
    >
      <UploadCloud size={22} className="text-muted group-hover:text-accent" />
      {names.length ? (
        <p className="text-[0.82rem] text-ink">
          {names.length === 1 ? names[0] : `${names.length} files selected`}
        </p>
      ) : (
        <p className="text-[0.82rem] text-muted">
          {hint ?? "Click to choose a file"}
        </p>
      )}
      <input
        ref={inputRef}
        type="file"
        name={name}
        accept={accept}
        multiple={multiple}
        required={required}
        className="sr-only"
        onChange={(event) =>
          setNames(Array.from(event.target.files ?? []).map((file) => file.name))
        }
      />
    </div>
  );
}
