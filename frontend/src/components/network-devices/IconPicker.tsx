"use client";

import { useRef, useState } from "react";
import useSWR from "swr";
import { apiFetch, getAccessToken, swrFetcher } from "@/lib/api";
import type { Envelope, IconData } from "./types";

const MAX_BYTES = 256 * 1024;

// Icon picker + uploader (plan §4.6). Lists the superadmin icon library, lets the
// user pick one, and uploads new .png/.svg via POST /topology/icons. The Flaticon
// helper is guidance only — nothing is fetched from Flaticon.
export function IconPicker({
  selectedId,
  onSelect,
}: {
  selectedId: string | null;
  onSelect: (icon: IconData | null) => void; // null = revert to default
}) {
  const { data, mutate } = useSWR<Envelope<IconData[]>>("/api/v1/topology/icons", swrFetcher, {
    revalidateOnFocus: false,
  });
  const fileRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const icons = data?.data || [];

  const upload = async (file: File) => {
    setError(null);
    if (!/\.(png|svg)$/i.test(file.name)) return setError("Only .png / .svg files.");
    if (file.size > MAX_BYTES) return setError("Icon exceeds 256 KB.");
    setUploading(true);
    try {
      const form = new FormData();
      form.append("file", file);
      // Raw fetch (not apiFetch) so the browser sets the multipart boundary; apiFetch
      // forces Content-Type: application/json, which breaks multipart parsing.
      const token = getAccessToken();
      const resp = await fetch(`/api/v1/topology/icons`, {
        method: "POST",
        body: form,
        credentials: "include",
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      if (!resp.ok) throw new Error(String(resp.status));
      const json = (await resp.json()) as Envelope<IconData>;
      await mutate();
      onSelect(json.data);
    } catch {
      setError("Upload failed.");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div>
      <div className="flex flex-wrap gap-2 max-h-28 overflow-y-auto p-1 rounded-md border border-border">
        <button
          type="button"
          onClick={() => onSelect(null)}
          className={`w-12 h-12 rounded-md border text-[10px] flex items-center justify-center ${
            !selectedId ? "border-foreground ring-2 ring-current" : "border-border"
          }`}
          title="Vendor default"
        >
          default
        </button>
        {icons.map((ic) => (
          <button
            key={ic.id}
            type="button"
            onClick={() => onSelect(ic)}
            title={ic.name}
            className={`w-12 h-12 rounded-md border p-1 flex items-center justify-center ${
              selectedId === ic.id ? "border-foreground ring-2 ring-current" : "border-border"
            }`}
          >
            {/* eslint-disable-next-line @next/next/no-img-element -- SVG via <img> can't run script */}
            <img src={ic.url} alt={ic.name} className="max-w-full max-h-full" />
          </button>
        ))}
      </div>

      <input
        ref={fileRef}
        type="file"
        accept=".png,.svg,image/png,image/svg+xml"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) upload(f);
          e.target.value = "";
        }}
      />
      <button
        type="button"
        onClick={() => fileRef.current?.click()}
        disabled={uploading}
        className="mt-2 px-2.5 py-1 text-xs rounded-md border border-border hover:bg-muted/50 disabled:opacity-50"
      >
        {uploading ? "Uploading…" : "⬆ Upload .png / .svg (≤ 256 KB)"}
      </button>
      {error ? <p className="text-[11px] text-red-500 mt-1">{error}</p> : null}
      <p className="text-[11px] text-muted-foreground mt-1">
        Need an icon? Get network-device icons at{" "}
        <a href="https://www.flaticon.com/" target="_blank" rel="noopener noreferrer" className="underline">
          flaticon.com
        </a>
        .
      </p>
    </div>
  );
}
