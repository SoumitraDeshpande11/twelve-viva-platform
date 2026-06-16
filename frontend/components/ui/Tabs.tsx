"use client";

import * as RadixTabs from "@radix-ui/react-tabs";
import { cn } from "../../lib/cn";

export function Tabs({
  value,
  onValueChange,
  items,
  className,
}: {
  value: string;
  onValueChange: (value: string) => void;
  items: { value: string; label: string }[];
  className?: string;
}) {
  return (
    <RadixTabs.Root value={value} onValueChange={onValueChange} className={className}>
      <RadixTabs.List className="flex flex-wrap gap-1 border-b border-line">
        {items.map((item) => (
          <RadixTabs.Trigger
            key={item.value}
            value={item.value}
            className={cn(
              "relative -mb-px px-3.5 py-2 text-[0.82rem] font-medium tracking-tight text-muted transition-colors",
              "hover:text-ink",
              "data-[state=active]:text-accent",
              "data-[state=active]:after:absolute data-[state=active]:after:inset-x-2 data-[state=active]:after:-bottom-px",
              "data-[state=active]:after:h-0.5 data-[state=active]:after:rounded-full data-[state=active]:after:bg-accent"
            )}
          >
            {item.label}
          </RadixTabs.Trigger>
        ))}
      </RadixTabs.List>
      {/*
        Panel content is rendered by the consumer outside this component, which
        otherwise leaves each Trigger's aria-controls pointing at nothing. We
        forceMount a tabpanel per item so the Radix-generated id exists and the
        aria-controls/labelledby association resolves for assistive tech. These
        wrappers carry no layout (sr-only) — the visible content stays external.
      */}
      {items.map((item) => (
        <RadixTabs.Content key={item.value} value={item.value} forceMount className="sr-only" />
      ))}
    </RadixTabs.Root>
  );
}
