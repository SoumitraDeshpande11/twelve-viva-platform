"use client";

import { ThemeProvider } from "next-themes";
import { IconContext } from "@phosphor-icons/react";

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ThemeProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
    >
      {/* Duotone weight gives the icons an editorial, two-tone character. */}
      <IconContext.Provider value={{ weight: "duotone" }}>{children}</IconContext.Provider>
    </ThemeProvider>
  );
}
