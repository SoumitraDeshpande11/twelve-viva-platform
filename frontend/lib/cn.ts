/** Minimal classname joiner — no runtime deps. Falsy entries are dropped. */
export type ClassValue = string | number | false | null | undefined;

export function cn(...values: ClassValue[]): string {
  return values.filter(Boolean).join(" ");
}
