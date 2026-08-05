"use client";

import { useState, type KeyboardEvent } from "react";
import { cn } from "@/lib/utils";

interface TagFilterFieldProps {
  label: string;
  values: string[];
  onChange: (values: string[]) => void;
  mono?: boolean;
  placeholder?: string;
}

// Polarity is encoded in the chip string with a leading "-" (exclude). This keeps the
// values: string[] contract unchanged, so pages that only read the array still compile;
// page query-builders split on the prefix into <param> / <param>_not. Domain filter values
// (app names, IPs, ports, AS orgs, interfaces) never start with "-", so no escape is needed.
export function chipParts(tag: string): { value: string; exclude: boolean } {
  const exclude = tag.startsWith("-");
  return { value: exclude ? tag.slice(1) : tag, exclude };
}

/** Split a field's chips into include + exclude value lists (used by page query-builders). */
export function splitChips(values: string[]): { include: string[]; exclude: string[] } {
  const include: string[] = [];
  const exclude: string[] = [];
  for (const t of values) {
    const { value, exclude: neg } = chipParts(t);
    if (!value) continue;
    (neg ? exclude : include).push(value);
  }
  return { include, exclude };
}

export function TagFilterField({ label, values, onChange, mono, placeholder = "Type and press Enter" }: TagFilterFieldProps) {
  const [input, setInput] = useState("");

  function addTag(tag: string) {
    const trimmed = tag.trim();
    if (trimmed && !values.includes(trimmed)) {
      onChange([...values, trimmed]);
    }
    setInput("");
  }

  function removeTag(index: number) {
    onChange(values.filter((_, i) => i !== index));
  }

  // Click a chip to flip include ↔ exclude (add/remove the leading "-").
  function toggleTag(index: number) {
    onChange(
      values.map((t, i) => {
        if (i !== index) return t;
        const { value, exclude } = chipParts(t);
        return exclude ? value : `-${value}`;
      })
    );
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      addTag(input);
    } else if (e.key === "Backspace" && !input && values.length > 0) {
      removeTag(values.length - 1);
    }
  }

  return (
    <div className="flex flex-col gap-1">
      <label className="text-[10px] text-muted-foreground uppercase font-medium">{label}</label>
      <div
        className={cn(
          "flex flex-wrap gap-1 px-1.5 py-1 text-xs rounded border border-border/60 dark:border-border/40 bg-background focus-within:ring-1 focus-within:ring-primary/30 min-h-[32px]",
          mono && "font-mono text-[11px]"
        )}
        onClick={() => {
          const el = document.getElementById(`tag-input-${label}`);
          el?.focus();
        }}
      >
        {values.map((tag, i) => {
          const { value, exclude } = chipParts(tag);
          return (
            <span
              key={i}
              onClick={(e) => {
                e.stopPropagation();
                toggleTag(i);
              }}
              title={exclude ? "Excluded — click to include" : "Included — click to exclude"}
              className={cn(
                "inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[11px] whitespace-nowrap cursor-pointer select-none",
                exclude
                  ? "bg-destructive/10 text-destructive line-through decoration-destructive/50"
                  : "bg-primary/10 text-primary"
              )}
            >
              {exclude && <span className="no-underline">−</span>}
              {value}
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  removeTag(i);
                }}
                className={cn("ml-0.5", exclude ? "text-destructive/60 hover:text-destructive" : "text-primary/60 hover:text-primary")}
              >
                ×
              </button>
            </span>
          );
        })}
        <input
          id={`tag-input-${label}`}
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={() => input && addTag(input)}
          placeholder={values.length === 0 ? placeholder : ""}
          className="flex-1 min-w-[80px] bg-transparent outline-none text-xs"
        />
      </div>
    </div>
  );
}
