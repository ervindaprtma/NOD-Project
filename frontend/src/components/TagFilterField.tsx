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
        {values.map((tag, i) => (
          <span
            key={i}
            className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-primary/10 text-primary text-[11px] whitespace-nowrap"
          >
            {tag}
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                removeTag(i);
              }}
              className="text-primary/60 hover:text-primary ml-0.5"
            >
              ×
            </button>
          </span>
        ))}
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
