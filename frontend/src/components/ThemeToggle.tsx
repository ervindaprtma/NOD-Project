"use client";

import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

/**
 * Dark/Light mode toggle button.
 * Uses next-themes useTheme() hook for state + persistence.
 * Shows a sun/moon icon with hover tooltip.
 */
export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);

  // Prevent hydration mismatch — only render after mount
  useEffect(() => setMounted(true), []);

  if (!mounted) {
    return (
      <button
        className="w-8 h-8 rounded-md border bg-card text-muted-foreground"
        aria-label="Toggle theme"
        disabled
      >
        <span className="text-xs">☀</span>
      </button>
    );
  }

  const isDark = theme === "dark";

  function toggle() {
    setTheme(isDark ? "light" : "dark");
  }

  return (
    <button
      onClick={toggle}
      className="w-8 h-8 flex items-center justify-center rounded-md border border-border bg-card hover:bg-muted transition-colors text-sm"
      title={isDark ? "Switch to light mode" : "Switch to dark mode"}
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
    >
      {isDark ? "☀" : "🌙"}
    </button>
  );
}
