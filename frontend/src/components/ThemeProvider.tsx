"use client";

import { ThemeProvider as NextThemesProvider } from "next-themes";
import type { ReactNode } from "react";

type ThemeProviderProps = {
  children: ReactNode;
};

/**
 * Wraps the app with next-themes ThemeProvider.
 * Enables dark/light/system mode with CSS class strategy.
 * Persists preference to localStorage and syncs with system.
 */
export function ThemeProvider({ children }: ThemeProviderProps) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange={false}
      storageKey="nod-theme"
      themes={["light", "dark"]}
    >
      {children}
    </NextThemesProvider>
  );
}
