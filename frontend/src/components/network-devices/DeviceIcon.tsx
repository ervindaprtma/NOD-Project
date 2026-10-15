import type { SVGProps } from "react";

// Built-in default icons for topology nodes — line-art, currentColor (theme-aware), no
// uploads. Custom uploaded icons are rendered as <img> by the caller, not here.
// Legacy/auto kinds alias onto a drawable type.
const ALIAS: Record<string, string> = { fg: "fw", sw: "sw_l2", srv: "srv_tower" };

export function DeviceIcon({ kind, className }: { kind: string; className?: string }) {
  const k = ALIAS[kind] || kind;
  const base: SVGProps<SVGSVGElement> = {
    viewBox: "0 0 24 24", fill: "none", stroke: "currentColor",
    strokeWidth: 1.7, strokeLinecap: "round", strokeLinejoin: "round",
  };
  const dot = { r: 0.7, fill: "currentColor", stroke: "none" } as const;

  switch (k) {
    case "pc":
      return <svg {...base} className={className}><rect x="3" y="4" width="18" height="12" rx="1" /><path d="M8 20h8M12 16v4" /></svg>;
    case "ap":
      return <svg {...base} className={className}><rect x="6" y="13" width="12" height="6" rx="1" /><circle cx="12" cy="16" {...dot} /><path d="M8.5 9.5a5 5 0 0 1 7 0M6 7a9 9 0 0 1 12 0" /></svg>;
    case "router":
      return <svg {...base} className={className}><rect x="3" y="13" width="18" height="6" rx="1" /><circle cx="7" cy="16" {...dot} /><circle cx="10" cy="16" {...dot} /><path d="M12 10V4m0 0-2 2m2-2 2 2M16 7h5m0 0-2-2m2 2-2 2" /></svg>;
    case "fw":
      return <svg {...base} className={className}><rect x="3" y="4" width="18" height="16" rx="1" /><path d="M3 9.3h18M3 14.6h18M9 4v5.3M15 9.3v5.3M9 14.6V20" /></svg>;
    case "cloud":
      return <svg {...base} className={className}><path d="M7.5 18h9a3.5 3.5 0 0 0 .4-7A5.5 5.5 0 0 0 6.3 11 3.5 3.5 0 0 0 7.5 18z" /></svg>;
    case "srv_tower":
      return <svg {...base} className={className}><rect x="7" y="3" width="10" height="18" rx="1" /><path d="M10 6.5h4M10 9.5h4" /><circle cx="12" cy="17.5" r="1" fill="currentColor" stroke="none" /></svg>;
    case "srv_rack":
      return <svg {...base} className={className}><rect x="4" y="4" width="16" height="16" rx="1" /><path d="M4 9.3h16M4 14.6h16" /><circle cx="7" cy="6.6" {...dot} /><circle cx="7" cy="11.9" {...dot} /><circle cx="7" cy="17.2" {...dot} /></svg>;
    case "sw_l2":
    case "sw_l3":
      return (
        <svg {...base} className={className}>
          <rect x="3" y="7" width="18" height="8" rx="1" />
          <path d="M6 15v2.4M9 15v2.4M12 15v2.4M15 15v2.4M18 15v2.4" />
          <text x="12" y="12.6" textAnchor="middle" fontSize="5.5" fontWeight="700" fill="currentColor" stroke="none">
            {k === "sw_l3" ? "L3" : "L2"}
          </text>
        </svg>
      );
    default:
      // unknown kind → neutral hexagon
      return <svg {...base} className={className}><path d="M12 2.5 20 7v10l-8 4.5L4 17V7z" /></svg>;
  }
}
