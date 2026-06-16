"use client";

import * as RadixSelect from "@radix-ui/react-select";
import { Check, CaretDown as ChevronDown } from "@phosphor-icons/react";
import { cn } from "../../lib/cn";

export interface SelectOption {
  value: string;
  label: string;
}

export function Select({
  value,
  onValueChange,
  options,
  placeholder = "Select…",
  id,
  className,
}: {
  value: string;
  onValueChange: (value: string) => void;
  options: SelectOption[];
  placeholder?: string;
  id?: string;
  className?: string;
}) {
  return (
    <RadixSelect.Root value={value || undefined} onValueChange={onValueChange}>
      <RadixSelect.Trigger
        id={id}
        className={cn(
          "flex h-10 w-full items-center justify-between gap-2 rounded-[var(--radius-control)] border border-line-strong bg-paper/60 px-3 text-[0.9rem] text-ink",
          "hover:border-accent/40 data-[placeholder]:text-muted/70",
          "focus-visible:outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/40",
          className
        )}
      >
        <RadixSelect.Value placeholder={placeholder} />
        <RadixSelect.Icon>
          <ChevronDown size={16} className="text-muted" />
        </RadixSelect.Icon>
      </RadixSelect.Trigger>
      <RadixSelect.Portal>
        <RadixSelect.Content
          position="popper"
          sideOffset={6}
          className="z-50 overflow-hidden rounded-[var(--radius-control)] border border-line bg-surface shadow-[var(--shadow-float)]"
        >
          <RadixSelect.Viewport className="p-1">
            {options.map((option) => (
              <RadixSelect.Item
                key={option.value}
                value={option.value}
                className={cn(
                  "flex cursor-pointer items-center justify-between gap-3 rounded-[6px] px-2.5 py-2 text-[0.85rem] text-ink-soft",
                  "data-[highlighted]:bg-accent-soft data-[highlighted]:text-accent data-[highlighted]:outline-none"
                )}
              >
                <RadixSelect.ItemText>{option.label}</RadixSelect.ItemText>
                <RadixSelect.ItemIndicator>
                  <Check size={14} />
                </RadixSelect.ItemIndicator>
              </RadixSelect.Item>
            ))}
          </RadixSelect.Viewport>
        </RadixSelect.Content>
      </RadixSelect.Portal>
    </RadixSelect.Root>
  );
}
