"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { apiFetch, swrFetcher, swrFetcherLong, hasMinRole } from "@/lib/api";
import type { DeviceRow, Envelope, IconData, TopologyData, TopologyLink, TopologyNode } from "./types";
import { DEFAULT_ICONS, LINK_STYLE, fmtRate } from "./types";
import { IconPicker } from "./IconPicker";
import { DeviceIcon } from "./DeviceIcon";

const NODE_W = 120;
const NODE_H = 56;
const GRID = 20;

// Preset link colours (each hex passes the backend _COLOR_RE). Default = null →
// the line inherits the muted colour, shown here as the grey swatch.
const LINK_COLORS: { label: string; value: string | null; swatch: string }[] = [
  { label: "Default (grey)", value: null, swatch: "#64748b" },
  { label: "Green", value: "#10b981", swatch: "#10b981" },
  { label: "Red", value: "#ef4444", swatch: "#ef4444" },
  { label: "Blue", value: "#3b82f6", swatch: "#3b82f6" },
  { label: "Purple", value: "#8b5cf6", swatch: "#8b5cf6" },
];

type DraftNode = Omit<TopologyNode, "online"> & { online?: boolean | null };
type DraftLink = TopologyLink;

const newId = () =>
  (crypto.randomUUID?.() || `${Date.now()}${Math.random()}`).replace(/-/g, "").slice(0, 32).padEnd(32, "0");
const snap = (v: number) => Math.round(v / GRID) * GRID;

// Canvas-only, display-only abbreviation of SNMP interface names (H3C/Comware style),
// used for the on-map Iface labels. Never applied to the stored if_name or the detail
// table — topology_live matches live traffic on the full tag.if_name, so the real value
// must stay intact. e.g. "Ten-GigabitEthernet3/1/20" → "XGE-3/1/20".
const shortIface = (n: string): string =>
  n.replace(/^Ten-?GigabitEthernet/i, "XGE-")
    .replace(/^(?:Forty-?GigabitEthernet|FortyGigE)/i, "FGE-")
    .replace(/^(?:Hundred-?GigabitEthernet|HundredGigE)/i, "HGE-")
    .replace(/^TwentyFive-?GigE/i, "XGE25-")
    .replace(/^GigabitEthernet/i, "GE-")
    .replace(/^Bridge-Aggregation/i, "BAGG")
    .replace(/^Route-Aggregation/i, "RAGG")
    .replace(/^Vlan-?interface/i, "Vlan");

export function TopologyCanvas({ onOpenDevice, refreshMs = 30_000 }: { onOpenDevice: (source: string) => void; refreshMs?: number }) {
  const { data, error, isLoading, mutate } = useSWR<Envelope<TopologyData>>(
    "/api/v1/topology",
    // topology_live is a multi-device OpenSearch aggregation — use the long (120s) fetcher
    // like the other heavy device components, so a slow poll doesn't abort at 30s and freeze
    // the map (which looked like "auto-refresh not working" on busier remote deployments).
    swrFetcherLong,
    { refreshInterval: refreshMs },
  );
  const canEdit = hasMinRole("superadmin");
  const [editing, setEditing] = useState(false);
  const [draftNodes, setDraftNodes] = useState<DraftNode[]>([]);
  const [draftLinks, setDraftLinks] = useState<DraftLink[]>([]);
  const [activeLink, setActiveLink] = useState<string | null>(null);
  // View-mode detail panel docked at the bottom: a clicked device (all its canvas cables)
  // or a single clicked cable. Replaces jumping to the Devices tab on node click.
  const [detail, setDetail] = useState<{ kind: "device" | "link"; id: string } | null>(null);
  const [modal, setModal] = useState<null | "device" | "text">(null);
  const [saving, setSaving] = useState(false);
  const [rotateMode, setRotateMode] = useState(false);  // toolbar toggle: drag a text node to rotate it
  const [selected, setSelected] = useState<Set<string>>(() => new Set()); // multi-select (edit mode)
  const [marquee, setMarquee] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  // Right-click context menu + edit flows (edit mode only).
  const [ctx, setCtx] = useState<{ x: number; y: number; nodeId: string | null } | null>(null);
  const [editNode, setEditNode] = useState<DraftNode | null>(null);   // Edit Device modal
  const [linksForNode, setLinksForNode] = useState<string | null>(null); // Edit Links manager (node id)
  const [linkEdit, setLinkEdit] = useState<null | { initial?: DraftLink; lockA?: string }>(null); // LinkModal
  // View toggles (per-viewer, persisted): link label = live traffic rate vs SNMP interface
  // name (e.g. xge-0/0/1); link shape = straight vs curved. localStorage is per-browser only.
  const [linkLabel, setLinkLabel] = useState<"speed" | "iface">(() => {
    try { return localStorage.getItem("nod.topo.linkLabel") === "iface" ? "iface" : "speed"; } catch { return "speed"; }
  });
  const [curved, setCurved] = useState(() => {
    try { return localStorage.getItem("nod.topo.curved") === "1"; } catch { return false; }
  });
  useEffect(() => { try { localStorage.setItem("nod.topo.linkLabel", linkLabel); } catch { /* private mode */ } }, [linkLabel]);
  useEffect(() => { try { localStorage.setItem("nod.topo.curved", curved ? "1" : "0"); } catch { /* private mode */ } }, [curved]);

  const updateNode = (id: string, patch: Partial<DraftNode>) =>
    setDraftNodes((p) => p.map((n) => (n.id === id ? { ...n, ...patch } : n)));
  const upsertLink = (l: DraftLink) =>
    setDraftLinks((p) => (p.some((x) => x.id === l.id) ? p.map((x) => (x.id === l.id ? l : x)) : [...p, l]));

  const topo = data?.data;
  // live map (read mode) vs. draft (edit mode)
  const nodes: (TopologyNode | DraftNode)[] = editing ? draftNodes : topo?.nodes || [];
  const links: (TopologyLink | DraftLink)[] = editing ? draftLinks : topo?.links || [];
  const nodeById = useMemo(() => {
    const m: Record<string, TopologyNode | DraftNode> = {};
    for (const n of nodes) m[n.id] = n;
    return m;
  }, [nodes]);
  // Links grouped by the unordered node pair → fan parallel links apart so multiple links
  // between the same two devices don't overlap.
  const pairGroups = useMemo(() => {
    const g: Record<string, string[]> = {};
    for (const l of links) {
      const key = [l.a_node_id, l.b_node_id].sort().join("|");
      (g[key] ||= []).push(l.id);
    }
    return g;
  }, [links]);

  const startEdit = () => {
    setDraftNodes((topo?.nodes || []).map((n) => ({ ...n })));
    setDraftLinks((topo?.links || []).map((l) => ({ ...l })));
    setEditing(true);
  };
  const cancelEdit = () => {
    setEditing(false);
    setModal(null);
    setRotateMode(false);
    setSelected(new Set());
  };
  const save = async () => {
    setSaving(true);
    try {
      await apiFetch("/api/v1/topology", {
        method: "PUT",
        body: JSON.stringify({
          nodes: draftNodes.map((n) => ({
            id: n.id, source_ip: n.source_ip, support: n.support,
            display_name: n.display_name, icon_kind: n.icon_kind, icon_id: n.icon_id,
            x: n.x, y: n.y, rotation: n.rotation || 0,
          })),
          links: draftLinks.map((l) => ({
            id: l.id, a_node_id: l.a_node_id, a_if_name: l.a_if_name,
            b_node_id: l.b_node_id, b_if_name: l.b_if_name, link_type: l.link_type,
            color: l.color || null,
          })),
        }),
      });
      setEditing(false);
      setRotateMode(false);
      setSelected(new Set());
      await mutate();
    } catch {
      alert("Save failed.");
    } finally {
      setSaving(false);
    }
  };

  // ── export / import (migration): dump or load the whole map as JSON ──
  const fileRef = useRef<HTMLInputElement | null>(null);
  const exportJson = () => {
    const srcN = editing ? draftNodes : topo?.nodes || [];
    const srcL = editing ? draftLinks : topo?.links || [];
    const payload = {
      version: 1,
      exported_at: new Date().toISOString(),
      nodes: srcN.map((n) => ({
        id: n.id, source_ip: n.source_ip, support: n.support, display_name: n.display_name,
        icon_kind: n.icon_kind, icon_id: n.icon_id, x: n.x, y: n.y, rotation: n.rotation || 0,
      })),
      links: srcL.map((l) => ({
        id: l.id, a_node_id: l.a_node_id, a_if_name: l.a_if_name, b_node_id: l.b_node_id,
        b_if_name: l.b_if_name, link_type: l.link_type, color: l.color || null,
      })),
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = `topology-${new Date().toISOString().slice(0, 10)}.json`; a.click();
    URL.revokeObjectURL(url);
  };
  // Import loads into the edit draft; the existing Save (PUT) persists it. Unknown icon_ids
  // are nulled server-side (icon library is per-environment) → node falls back to its icon_kind.
  const importJson = (file: File) => {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const data = JSON.parse(String(reader.result));
        if (!Array.isArray(data?.nodes) || !Array.isArray(data?.links)) throw new Error("shape");
        setDraftNodes(data.nodes.map((n: DraftNode) => ({ ...n, rotation: n.rotation || 0 })));
        setDraftLinks(data.links.map((l: DraftLink) => ({
          ...l, a_oper: null, b_oper: null, rx_mbps: 0, tx_mbps: 0,
        })));
        if (!editing) setEditing(true);
      } catch {
        alert("Invalid topology file — expected a JSON export with nodes and links.");
      }
    };
    reader.readAsText(file);
  };

  // ── capture the full canvas (unscaled) to PNG/JPG ──
  const captureRef = useRef<HTMLDivElement | null>(null);
  const [capturing, setCapturing] = useState(false);
  const capture = async (fmt: "png" | "jpg") => {
    const node = captureRef.current;
    if (!node) return;
    setCapturing(true);
    try {
      const { toPng, toJpeg } = await import("html-to-image");  // code-split: only loaded on use
      const bg = getComputedStyle(document.body).backgroundColor || "#ffffff";
      // Override the live zoom transform so the export is always full-size at 2× for crispness.
      const opts = {
        width: w, height: h, pixelRatio: 2, backgroundColor: bg,
        style: { transform: "scale(1)", transformOrigin: "0 0", width: `${w}px`, height: `${h}px` },
      };
      const dataUrl = fmt === "jpg" ? await toJpeg(node, { ...opts, quality: 0.95 }) : await toPng(node, opts);
      const a = document.createElement("a");
      a.href = dataUrl; a.download = `topology-${new Date().toISOString().slice(0, 10)}.${fmt}`; a.click();
    } catch {
      alert("Capture failed.");
    } finally {
      setCapturing(false);
    }
  };

  // ── pan-zoom ──
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [zoom, setZoom] = useState(1);
  const clampZoom = (z: number) => Math.min(2.5, Math.max(0.3, z));
  // Ctrl/⌘ + wheel zooms (plain wheel still scrolls). Non-passive so we can preventDefault.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      setZoom((z) => clampZoom(z * (e.deltaY < 0 ? 1.1 : 0.9)));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  // ── drag (edit mode only) — zoom-aware: screen delta ÷ zoom = canvas delta ──
  // A drag moves every selected node together (items = their start positions).
  const dragRef = useRef<{ startX: number; startY: number; items: { id: string; x: number; y: number }[] } | null>(null);
  // Rotate-drag for text nodes: store the node's on-screen centre, then angle = atan2(pointer−centre).
  const rotRef = useRef<{ id: string; cx: number; cy: number } | null>(null);
  // Marquee (rubber-band) selection: start corner in canvas coords + whether to add to the set.
  const marqRef = useRef<{ x0: number; y0: number; additive: boolean } | null>(null);
  // Screen (client) point → canvas coords: captureRef is the scaled inner layer.
  const toCanvas = (clientX: number, clientY: number) => {
    const r = captureRef.current?.getBoundingClientRect();
    return { x: (clientX - (r?.left ?? 0)) / zoom, y: (clientY - (r?.top ?? 0)) / zoom };
  };
  const startRotate = (e: React.PointerEvent, n: DraftNode) => {
    if (!editing) return;
    e.preventDefault();
    e.stopPropagation();
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    const rect = captureRef.current?.getBoundingClientRect();
    const cx = (rect ? rect.left : 0) + (n.x + NODE_W / 2) * zoom;
    const cy = (rect ? rect.top : 0) + (n.y + NODE_H / 2) * zoom;
    rotRef.current = { id: n.id, cx, cy };
  };
  const onNodePointerDown = (e: React.PointerEvent, n: DraftNode) => {
    if (!editing || e.button !== 0) return;
    // Rotate mode: dragging a text node's body rotates it instead of moving.
    if (rotateMode && n.icon_kind === "text") { startRotate(e, n); return; }
    e.preventDefault();
    e.stopPropagation();  // don't let the background start a marquee underneath
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    // Selection: Shift toggles; plain click on an unselected node selects just it;
    // plain click on an already-selected node keeps the group (so you can drag it).
    let ids = selected;
    if (e.shiftKey) {
      ids = new Set(selected);
      if (ids.has(n.id)) ids.delete(n.id); else ids.add(n.id);
      setSelected(ids);
    } else if (!selected.has(n.id)) {
      ids = new Set([n.id]);
      setSelected(ids);
    }
    const moveIds = ids.size ? ids : new Set([n.id]);
    dragRef.current = {
      startX: e.clientX, startY: e.clientY,
      items: draftNodes.filter((m) => moveIds.has(m.id)).map((m) => ({ id: m.id, x: m.x, y: m.y })),
    };
  };
  // Pointer down on empty canvas → begin a marquee (Shift adds to the current selection).
  const onCanvasPointerDown = (e: React.PointerEvent) => {
    if (!editing || e.button !== 0) return;
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    const { x, y } = toCanvas(e.clientX, e.clientY);
    marqRef.current = { x0: x, y0: y, additive: e.shiftKey };
    setMarquee({ x0: x, y0: y, x1: x, y1: y });
    if (!e.shiftKey) setSelected(new Set());
  };
  const onCanvasPointerMove = (e: React.PointerEvent) => {
    const m = marqRef.current;
    if (m) {
      const { x, y } = toCanvas(e.clientX, e.clientY);
      setMarquee({ x0: m.x0, y0: m.y0, x1: x, y1: y });
      return;
    }
    const r = rotRef.current;
    if (r) {
      // Handle points up at 0°; add 90 so "up" reads as upright. Snap 15° unless Shift held.
      let deg = Math.atan2(e.clientY - r.cy, e.clientX - r.cx) * 180 / Math.PI + 90;
      if (!e.shiftKey) deg = Math.round(deg / 15) * 15;
      deg = ((Math.round(deg) % 360) + 360) % 360;
      setDraftNodes((prev) => prev.map((n) => (n.id === r.id ? { ...n, rotation: deg } : n)));
      return;
    }
    const d = dragRef.current;
    if (!d) return;
    const dx = (e.clientX - d.startX) / zoom, dy = (e.clientY - d.startY) / zoom;
    setDraftNodes((prev) => prev.map((n) => {
      const it = d.items.find((i) => i.id === n.id);
      return it ? { ...n, x: Math.max(0, snap(it.x + dx)), y: Math.max(0, snap(it.y + dy)) } : n;
    }));
  };
  const onCanvasPointerUp = () => {
    const m = marqRef.current;
    if (m && marquee) {
      const minX = Math.min(m.x0, marquee.x1), maxX = Math.max(m.x0, marquee.x1);
      const minY = Math.min(m.y0, marquee.y1), maxY = Math.max(m.y0, marquee.y1);
      const hit = draftNodes
        .filter((n) => n.x < maxX && n.x + NODE_W > minX && n.y < maxY && n.y + NODE_H > minY)
        .map((n) => n.id);
      setSelected((prev) => {
        const s = m.additive ? new Set(prev) : new Set<string>();
        hit.forEach((id) => s.add(id));
        return s;
      });
    }
    marqRef.current = null; setMarquee(null);
    dragRef.current = null; rotRef.current = null;
  };

  const { w, h } = useMemo(() => {
    let mw = 600, mh = 400;
    for (const n of nodes) { mw = Math.max(mw, n.x + NODE_W + 40); mh = Math.max(mh, n.y + NODE_H + 40); }
    return { w: mw, h: mh };
  }, [nodes]);

  // Fit the whole map into the current viewport (never upscale past 100%).
  const fitZoom = () => {
    const el = scrollRef.current;
    if (!el) return;
    setZoom(clampZoom(Math.min(el.clientWidth / w, el.clientHeight / h, 1)));
  };

  const deleteNode = (id: string) => {
    setDraftNodes((p) => p.filter((n) => n.id !== id));
    setDraftLinks((p) => p.filter((l) => l.a_node_id !== id && l.b_node_id !== id));
  };
  const deleteLink = (id: string) => setDraftLinks((p) => p.filter((l) => l.id !== id));

  // Right-click: superadmin only. Read mode offers "Edit Layout"; edit mode offers the
  // add/edit actions (empty canvas → add; on a node → edit).
  const openCtx = (e: React.MouseEvent, nodeId: string | null) => {
    if (!canEdit) return;
    e.preventDefault();
    e.stopPropagation();
    setCtx({ x: e.clientX, y: e.clientY, nodeId });
  };

  if (isLoading) return <p className="text-sm text-muted-foreground py-8 text-center">Loading topology…</p>;
  if (error) return <p className="text-sm text-red-500 py-8 text-center">Failed to load topology.</p>;

  const center = (n: { x: number; y: number }) => ({ cx: n.x + NODE_W / 2, cy: n.y + NODE_H / 2 });
  const endPoint = (from: { x: number; y: number }, to: { x: number; y: number }) => {
    const a = center(from), b = center(to);
    const dx = b.cx - a.cx, dy = b.cy - a.cy, len = Math.hypot(dx, dy) || 1, off = NODE_H / 2 + 6;
    return { x: a.cx + (dx / len) * off, y: a.cy + (dy / len) * off };
  };
  const dotColor = (oper: string | null | undefined, support: boolean) =>
    support ? "transparent" : oper === "up" ? "#10b981" : oper === "down" ? "#ef4444" : "#a1a1aa";

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
        <Legend />
        <div className="flex items-center gap-2">
          {/* Zoom controls (works in view + edit). Ctrl/⌘ + wheel also zooms. */}
          <div className="inline-flex items-center rounded-md border border-border overflow-hidden text-xs">
            <button onClick={() => setZoom((z) => clampZoom(z * 0.9))} className="px-2 py-1 hover:bg-muted/50" title="Zoom out">−</button>
            <button onClick={() => setZoom(1)} className="px-2 py-1 hover:bg-muted/50 tabular-nums w-12 text-center" title="Reset to 100%">{Math.round(zoom * 100)}%</button>
            <button onClick={() => setZoom((z) => clampZoom(z * 1.1))} className="px-2 py-1 hover:bg-muted/50" title="Zoom in">+</button>
            <button onClick={fitZoom} className="px-2 py-1 hover:bg-muted/50 border-l border-border" title="Fit to screen">Fit</button>
          </div>
          {/* Link display toggles (view mode): label = speed/interface, shape = straight/curved. */}
          {!editing ? (
            <>
              <ToolbarBtn onClick={() => setLinkLabel((m) => (m === "speed" ? "iface" : "speed"))}
                title="Link labels: live traffic speed or SNMP interface name">
                {linkLabel === "speed" ? "🏷 Speed" : "🏷 Iface"}
              </ToolbarBtn>
              <ToolbarBtn onClick={() => setCurved((v) => !v)} primary={curved}
                title="Link shape: straight or curved">
                {curved ? "⌇ Curved" : "╱ Straight"}
              </ToolbarBtn>
            </>
          ) : null}
          {/* Migration: export the whole map to JSON, or import one (superadmin → loads into edit). */}
          <ToolbarBtn onClick={exportJson}>⬇ Export</ToolbarBtn>
          {canEdit ? <ToolbarBtn onClick={() => fileRef.current?.click()}>⬆ Import</ToolbarBtn> : null}
          {/* Capture a clean image — view mode only (edit mode shows the delete-× overlays). */}
          {!editing && nodes.length > 0 ? (
            <>
              <ToolbarBtn onClick={() => capture("png")} disabled={capturing}>{capturing ? "…" : "📷 PNG"}</ToolbarBtn>
              <ToolbarBtn onClick={() => capture("jpg")} disabled={capturing}>JPG</ToolbarBtn>
            </>
          ) : null}
          <input ref={fileRef} type="file" accept="application/json,.json" className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) importJson(f); e.target.value = ""; }} />
          {canEdit ? (
            editing ? (
              <div className="flex gap-2">
                <ToolbarBtn onClick={() => setModal("device")}>+ Device</ToolbarBtn>
                <ToolbarBtn onClick={() => setLinkEdit({})}>+ Link</ToolbarBtn>
                <ToolbarBtn onClick={() => setModal("text")}>+ Text</ToolbarBtn>
                <ToolbarBtn onClick={() => setRotateMode((v) => !v)} primary={rotateMode}
                  title="Rotate mode: drag a text node to rotate it">⟳ Rotate</ToolbarBtn>
                <ToolbarBtn onClick={save} disabled={saving} primary>{saving ? "Saving…" : "Save"}</ToolbarBtn>
                <ToolbarBtn onClick={cancelEdit}>Cancel</ToolbarBtn>
              </div>
            ) : (
              <ToolbarBtn onClick={startEdit}>✎ Edit Layout</ToolbarBtn>
            )
          ) : null}
        </div>
      </div>

      {editing ? <UnmappedBanner draftNodes={draftNodes} onAdd={(d) => addDeviceNode(d, setDraftNodes, draftNodes)} /> : null}

      {nodes.length === 0 && !editing ? (
        <div className="rounded-lg border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
          No topology yet. {canEdit ? "Click “Edit Layout” to add devices." : "A superadmin can add devices."}
        </div>
      ) : (
        <div
          ref={scrollRef}
          className="relative overflow-auto rounded-lg border border-border bg-muted/10"
          // Viewport-sized box: scroll (both axes) stays INSIDE the topology, so the page
          // doesn't grow with the map. Height tracks the viewport (dynamic).
          style={{ height: "calc(100vh - 16rem)", minHeight: 420 }}
          onPointerDown={onCanvasPointerDown}
          onPointerMove={onCanvasPointerMove}
          onPointerUp={onCanvasPointerUp}
          onContextMenu={(e) => openCtx(e, null)}
        >
          {/* Outer box reserves the scaled size so scrollbars track the zoom; inner scales. */}
          <div style={{ width: w * zoom, height: h * zoom }}>
          <div ref={captureRef} className="relative" style={{ width: w, height: h, transform: `scale(${zoom})`, transformOrigin: "0 0" }}>
            <svg className="absolute inset-0" width={w} height={h}>
              {links.map((lk) => {
                const a = nodeById[lk.a_node_id], b = nodeById[lk.b_node_id];
                if (!a || !b) return null;
                const ca = center(a), cb = center(b), style = LINK_STYLE[lk.link_type] || LINK_STYLE.lan;
                const ea = endPoint(a, b), eb = endPoint(b, a);
                const lkR = lk as TopologyLink;
                const vx = cb.cx - ca.cx, vy = cb.cy - ca.cy, vlen = Math.hypot(vx, vy) || 1;
                const ux = vx / vlen, uy = vy / vlen, nx = -uy, ny = ux;
                // Fan parallel links apart: shift this link perpendicular by its slot in the pair.
                const grp = pairGroups[[lk.a_node_id, lk.b_node_id].sort().join("|")] || [lk.id];
                const slot = grp.indexOf(lk.id) - (grp.length - 1) / 2;
                const ox = nx * slot * 24, oy = ny * slot * 24;
                const p1 = { x: ca.cx + ox, y: ca.cy + oy }, p2 = { x: cb.cx + ox, y: cb.cy + oy };
                const da = { x: ea.x + ox, y: ea.y + oy }, dbp = { x: eb.x + ox, y: eb.y + oy };
                // Label anchors: nudge each end's rate inward + off the line, on the link's outer side.
                const IN_OFF = 22, PERP_OFF = 13, sp = slot >= 0 ? 1 : -1;
                const aLbl = { x: da.x + ux * IN_OFF + nx * PERP_OFF * sp, y: da.y + uy * IN_OFF + ny * PERP_OFF * sp };
                const bLbl = { x: dbp.x - ux * IN_OFF + nx * PERP_OFF * sp, y: dbp.y - uy * IN_OFF + ny * PERP_OFF * sp };
                // Curved mode: quadratic bézier bowed perpendicular from the midpoint; parallel
                // links bow on their fan side so they stay separated. Straight mode: a plain line.
                const bend = Math.min(70, vlen * 0.2) * (slot >= 0 ? 1 : -1);
                const mx = (p1.x + p2.x) / 2 + nx * bend, my = (p1.y + p2.y) / 2 + ny * bend;
                const d = curved
                  ? `M ${p1.x} ${p1.y} Q ${mx} ${my} ${p2.x} ${p2.y}`
                  : `M ${p1.x} ${p1.y} L ${p2.x} ${p2.y}`;
                return (
                  <g key={lk.id} className="cursor-pointer" onClick={() => editing
                    ? setActiveLink(activeLink === lk.id ? null : lk.id)
                    : setDetail((d) => d?.kind === "link" && d.id === lk.id ? null : { kind: "link", id: lk.id })}>
                    <path d={d} fill="none" stroke={lk.color || "currentColor"}
                      className={lk.color ? "" : "text-muted-foreground"} strokeWidth={style.width} strokeDasharray={style.dash} />
                    <circle cx={da.x} cy={da.y} r={5} fill={dotColor(lkR.a_oper, a.support)}
                      stroke={a.support ? "#a1a1aa" : "none"} strokeDasharray={a.support ? "2 2" : undefined} />
                    <circle cx={dbp.x} cy={dbp.y} r={5} fill={dotColor(lkR.b_oper, b.support)}
                      stroke={b.support ? "#a1a1aa" : "none"} strokeDasharray={b.support ? "2 2" : undefined} />
                    {/* Per end: either live traffic (↓ IN blue, ↑ OUT orange — support ends have
                        no rate) or the SNMP interface name, driven by the label toggle. */}
                    {!editing && linkLabel === "speed" && lkR.a_rx_mbps != null ? (
                      <LinkEndRate x={aLbl.x} y={aLbl.y} rx={lkR.a_rx_mbps} tx={lkR.a_tx_mbps ?? 0} />
                    ) : null}
                    {!editing && linkLabel === "speed" && lkR.b_rx_mbps != null ? (
                      <LinkEndRate x={bLbl.x} y={bLbl.y} rx={lkR.b_rx_mbps} tx={lkR.b_tx_mbps ?? 0} />
                    ) : null}
                    {!editing && linkLabel === "iface" && lk.a_if_name ? (
                      <LinkEndLabel x={aLbl.x} y={aLbl.y} text={shortIface(lk.a_if_name)} />
                    ) : null}
                    {!editing && linkLabel === "iface" && lk.b_if_name ? (
                      <LinkEndLabel x={bLbl.x} y={bLbl.y} text={shortIface(lk.b_if_name)} />
                    ) : null}
                  </g>
                );
              })}
            </svg>

            {nodes.map((n) => (
              <div
                key={n.id}
                onPointerDown={(e) => onNodePointerDown(e, n as DraftNode)}
                onClick={() => { if (!editing && n.icon_kind !== "text") setDetail((d) => d?.kind === "device" && d.id === n.id ? null : { kind: "device", id: n.id }); }}
                onContextMenu={(e) => openCtx(e, n.id)}
                title={n.display_name || n.source_ip || ""}
                className={`absolute flex flex-col items-center justify-center text-center px-1 ${
                  editing ? "cursor-move" : n.icon_kind !== "text" ? "cursor-pointer hover:opacity-80" : ""
                } ${(editing && selected.has(n.id)) || (!editing && detail?.kind === "device" && detail.id === n.id) ? "rounded ring-2 ring-primary ring-offset-1 ring-offset-background" : ""}`}
                style={{ left: n.x, top: n.y, width: NODE_W, height: NODE_H, touchAction: "none" }}
              >
                {n.icon_kind === "text" ? (
                  /* Text annotation node — text + (edit mode) a drag-to-rotate handle. */
                  <>
                    <span className="inline-block text-[12px] font-semibold text-foreground whitespace-pre-wrap break-words max-w-full rounded bg-background/70 px-1.5 py-0.5"
                      style={{ transform: `rotate(${n.rotation || 0}deg)` }}>
                      {n.display_name || "Text"}
                    </span>
                    {editing ? (
                      <>
                        <span
                          onPointerDown={(e) => startRotate(e, n as DraftNode)}
                          title="Drag to rotate (hold Shift for free angle)"
                          className="absolute left-1/2 -translate-x-1/2 -top-3 w-3 h-3 rounded-full bg-primary ring-2 ring-background cursor-grab"
                          style={{ touchAction: "none" }}
                        />
                        <span className="absolute left-1/2 -translate-x-1/2 -top-7 text-[9px] tabular-nums px-1 rounded bg-background/80 text-muted-foreground pointer-events-none">
                          {n.rotation || 0}°
                        </span>
                      </>
                    ) : null}
                  </>
                ) : (
                  <>
                    {/* No box/border — just the icon. Status (online/offline) sits as a dot on
                        the icon's corner; support nodes have no state so no dot. */}
                    <span className="relative inline-flex">
                      <NodeGlyph iconUrl={n.icon_url} iconKind={n.icon_kind} />
                      {!n.support ? (
                        <span className="absolute -top-0.5 -right-1 w-2.5 h-2.5 rounded-full ring-2 ring-background"
                          style={{ background: n.online ? "#10b981" : "#ef4444" }} />
                      ) : null}
                    </span>
                    <span className="mt-0.5 text-[11px] font-medium truncate max-w-full rounded bg-background/70 px-1">
                      {n.display_name || n.source_ip}
                    </span>
                  </>
                )}
                {editing ? (
                  <button
                    onClick={(e) => { e.stopPropagation(); deleteNode(n.id); }}
                    className="absolute -top-2 -right-2 w-4 h-4 rounded-full bg-red-500 text-white text-[10px] leading-none"
                    title="Remove from map"
                  >×</button>
                ) : null}
              </div>
            ))}

            {marquee ? (
              <div className="absolute border border-primary bg-primary/10 pointer-events-none"
                style={{
                  left: Math.min(marquee.x0, marquee.x1), top: Math.min(marquee.y0, marquee.y1),
                  width: Math.abs(marquee.x1 - marquee.x0), height: Math.abs(marquee.y1 - marquee.y0),
                }} />
            ) : null}
          </div>
          </div>
        </div>
      )}

      {editing && activeLink ? (() => {
        const lk = links.find((l) => l.id === activeLink);
        if (!lk) return null;
        return <LinkPanel link={lk as TopologyLink} nodeById={nodeById} editing={editing}
          onClose={() => setActiveLink(null)} onDelete={() => { deleteLink(lk.id); setActiveLink(null); }} />;
      })() : null}

      {!editing && detail ? (
        <LinkDetailTable detail={detail} links={links as TopologyLink[]} nodeById={nodeById}
          onClose={() => setDetail(null)} onOpenDevice={onOpenDevice} />
      ) : null}

      {modal === "device" ? (
        <AddDeviceModal
          placed={new Set(draftNodes.map((n) => n.source_ip).filter(Boolean) as string[])}
          onClose={() => setModal(null)}
          onAdd={(node) => {
            // Stagger each add onto a free grid slot (modal hands x:40,y:40 — would stack).
            setDraftNodes((p) => [...p, { ...node, x: 40 + (p.length % 5) * 150, y: 40 + Math.floor(p.length / 5) * 100 }]);
            setModal(null);
          }}
        />
      ) : null}

      {modal === "text" ? (
        <TextModal
          onClose={() => setModal(null)}
          onAdd={(text, rotation) => {
            setDraftNodes((p) => [...p, {
              id: newId(), source_ip: null, support: true, display_name: text,
              icon_kind: "text", icon_id: null, rotation,
              x: 40 + (p.length % 5) * 150, y: 40 + Math.floor(p.length / 5) * 100,
            }]);
            setModal(null);
          }}
        />
      ) : null}

      {/* Right-click context menu */}
      {ctx ? (
        <ContextMenu
          x={ctx.x}
          y={ctx.y}
          onClose={() => setCtx(null)}
          items={
            !editing
              ? [{ label: "✎ Edit Layout", on: startEdit }]
              : ctx.nodeId == null
              ? [
                  { label: "➕ Add Device", on: () => setModal("device") },
                  { label: "🔗 Add Link", on: () => setLinkEdit({}) },
                  { label: "📝 Add Text", on: () => setModal("text") },
                ]
              : (() => {
                  const tn = draftNodes.find((n) => n.id === ctx.nodeId) as DraftNode | undefined;
                  const isText = tn?.icon_kind === "text";
                  return [
                    { label: isText ? "✎ Edit Text" : "✎ Edit Device", on: () => setEditNode(tn || null) },
                    // Text annotations don't carry interfaces, so no link management.
                    ...(isText ? [] : [{ label: "🔗 Edit Link", on: () => setLinksForNode(ctx.nodeId as string) }]),
                    { label: isText ? "🗑 Delete Text" : "🗑 Delete Device", on: () => deleteNode(ctx.nodeId as string), danger: true },
                  ];
                })()
          }
        />
      ) : null}

      {editNode ? (
        <EditDeviceModal
          node={editNode}
          onClose={() => setEditNode(null)}
          onSave={(patch) => { updateNode(editNode.id, patch); setEditNode(null); }}
        />
      ) : null}

      {linksForNode ? (
        <EditLinksModal
          nodeId={linksForNode}
          nodes={draftNodes}
          links={draftLinks}
          onClose={() => setLinksForNode(null)}
          onEditLink={(l) => { setLinksForNode(null); setLinkEdit({ initial: l }); }}
          onNewLink={(a) => { setLinksForNode(null); setLinkEdit({ lockA: a }); }}
          onDeleteLink={(id) => deleteLink(id)}
        />
      ) : null}

      {linkEdit ? (
        <LinkModal
          nodes={draftNodes}
          initial={linkEdit.initial}
          lockA={linkEdit.lockA}
          onClose={() => setLinkEdit(null)}
          onSave={(link) => { upsertLink(link); setLinkEdit(null); }}
        />
      ) : null}
    </div>
  );
}

function ContextMenu({ x, y, items, onClose }: {
  x: number; y: number;
  items: { label: string; on: () => void; danger?: boolean }[];
  onClose: () => void;
}) {
  return (
    // Full-screen backdrop closes the menu on any click / right-click elsewhere.
    <div className="fixed inset-0 z-50" onClick={onClose} onContextMenu={(e) => { e.preventDefault(); onClose(); }}>
      <div
        className="absolute min-w-[160px] rounded-md border border-border bg-background shadow-lg py-1 text-sm"
        style={{ left: x, top: y }}
        onClick={(e) => e.stopPropagation()}
      >
        {items.map((it) => (
          <button
            key={it.label}
            onClick={() => { it.on(); onClose(); }}
            className={`block w-full text-left px-3 py-1.5 hover:bg-muted/60 ${it.danger ? "text-red-500" : ""}`}
          >
            {it.label}
          </button>
        ))}
      </div>
    </div>
  );
}

// append a data node for a roster device at a free-ish slot
function addDeviceNode(d: DeviceRow, setNodes: React.Dispatch<React.SetStateAction<DraftNode[]>>, existing: DraftNode[]) {
  const n = existing.length;
  setNodes((p) => [...p, {
    id: newId(), source_ip: d.source_ip, support: false, display_name: d.name,
    icon_kind: d.vendor === "fortigate" ? "fg" : "sw", icon_id: null,
    x: 40 + (n % 5) * 150, y: 40 + Math.floor(n / 5) * 100,
  }]);
}

// A node's glyph: the uploaded icon, or the built-in default. If the uploaded file 404s
// (deleted from storage, or a stale DB row after a redeploy), fall back to the default
// instead of a broken image.
function NodeGlyph({ iconUrl, iconKind }: { iconUrl?: string | null; iconKind: string }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => { setFailed(false); }, [iconUrl]);  // retry when the url changes
  if (iconUrl && !failed) {
    // eslint-disable-next-line @next/next/no-img-element -- SVG via <img> can't run script
    return <img src={iconUrl} alt="" onError={() => setFailed(true)}
      className="h-8 w-8 object-contain pointer-events-none" />;
  }
  return <DeviceIcon kind={iconKind} className="h-8 w-8 pointer-events-none" />;
}

function ToolbarBtn({ children, onClick, primary, disabled, title }: { children: React.ReactNode; onClick: () => void; primary?: boolean; disabled?: boolean; title?: string }) {
  return (
    <button onClick={onClick} disabled={disabled} title={title}
      className={`px-2.5 py-1 text-xs rounded-md border transition disabled:opacity-50 ${
        primary ? "bg-foreground text-background border-foreground" : "border-border hover:bg-muted/50"
      }`}>{children}</button>
  );
}

// Per-interface rate label on a link end: ↓ IN blue, ↑ OUT orange (Interface Bandwidth colours).
// A background-coloured stroke behind the glyphs keeps them readable over the link line.
function LinkEndRate({ x, y, rx, tx }: { x: number; y: number; rx: number; tx: number }) {
  return (
    <text textAnchor="middle" className="text-[10px] font-semibold"
      style={{ paintOrder: "stroke" }} stroke="hsl(var(--background))" strokeWidth={3.5}>
      {/* Stacked, not inline — IN over OUT, so neither direction is cramped on the line. */}
      <tspan x={x} y={y} fill="#3b82f6">↓ {fmtRate(rx)}</tspan>
      <tspan x={x} y={y} dy={12} fill="#f97316">↑ {fmtRate(tx)}</tspan>
    </text>
  );
}

// SNMP interface-name label on a link end (if_name verbatim, e.g. "xge-0/0/1"). Same
// background stroke as the rate label so it stays readable over the link.
function LinkEndLabel({ x, y, text }: { x: number; y: number; text: string }) {
  return (
    <text x={x} y={y} textAnchor="middle" className="text-[10px] font-semibold"
      fill="hsl(var(--foreground))" style={{ paintOrder: "stroke" }}
      stroke="hsl(var(--background))" strokeWidth={3.5}>
      {text}
    </text>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
      <span>🟢 has data</span><span>🔴 no data</span><span>◻ support</span>
      <span>— LAN</span><span>━ WAN</span><span>┄ MPLS</span><span>··· Wireless</span>
    </div>
  );
}

function UnmappedBanner({ draftNodes, onAdd }: { draftNodes: DraftNode[]; onAdd: (d: DeviceRow) => void }) {
  const { data } = useSWR<Envelope<DeviceRow[]>>("/api/v1/network-devices/devices", swrFetcher);
  const placed = new Set(draftNodes.map((n) => n.source_ip).filter(Boolean));
  const unmapped = (data?.data || []).filter((d) => !placed.has(d.source_ip));
  if (unmapped.length === 0) return null;
  return (
    <div className="mb-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs flex items-center justify-between">
      <span>{unmapped.length} device(s) have SNMP data but are not on the map.</span>
      <button onClick={() => unmapped.forEach(onAdd)} className="underline">Add them</button>
    </div>
  );
}

function LinkPanel({ link, nodeById, editing, onClose, onDelete }: {
  link: TopologyLink; nodeById: Record<string, TopologyNode | DraftNode>; editing: boolean;
  onClose: () => void; onDelete: () => void;
}) {
  const a = nodeById[link.a_node_id], b = nodeById[link.b_node_id];
  const nm = (n: TopologyNode | DraftNode | undefined, iff: string | null) =>
    `${n?.display_name || n?.source_ip || "?"}${iff ? ` · ${iff}` : ""}`;
  return (
    <div className="mt-3 rounded-md border border-border p-3 text-xs">
      <div className="flex items-center justify-between">
        <span className="font-semibold uppercase">{link.link_type} link</span>
        <button onClick={onClose} className="text-muted-foreground hover:text-foreground">✕</button>
      </div>
      <div className="mt-1.5 space-y-0.5">
        <div>{nm(a, link.a_if_name)} — {link.a_oper ?? "support"}</div>
        <div>{nm(b, link.b_if_name)} — {link.b_oper ?? "support"}</div>
        {!editing ? (
          <div>
            <span className="text-blue-600 dark:text-blue-400">↓ IN {fmtRate(link.rx_mbps)}</span>
            {" · "}
            <span className="text-orange-600 dark:text-orange-400">↑ OUT {fmtRate(link.tx_mbps)}</span>
          </div>
        ) : null}
      </div>
      {editing ? <button onClick={onDelete} className="mt-2 text-red-500 hover:underline">Delete link</button> : null}
    </div>
  );
}

// One end of a link, normalised so the clicked device is always "near".
function linkEnd(lk: TopologyLink, side: "a" | "b") {
  return side === "a"
    ? { nodeId: lk.a_node_id, iff: lk.a_if_name, oper: lk.a_oper, rx: lk.a_rx_mbps, tx: lk.a_tx_mbps }
    : { nodeId: lk.b_node_id, iff: lk.b_if_name, oper: lk.b_oper, rx: lk.b_rx_mbps, tx: lk.b_tx_mbps };
}

// Bottom detail table: for a clicked DEVICE, every canvas cable touching it (clicked device =
// "near", the other end = "connected"); for a clicked LINK, that one cable. Status + live
// traffic come straight off the link — no extra fetch. ↗ opens the full device detail.
function LinkDetailTable({ detail, links, nodeById, onClose, onOpenDevice }: {
  detail: { kind: "device" | "link"; id: string };
  links: TopologyLink[]; nodeById: Record<string, TopologyNode | DraftNode>;
  onClose: () => void; onOpenDevice: (source: string) => void;
}) {
  const nameOf = (id: string) => nodeById[id]?.display_name || nodeById[id]?.source_ip || "?";
  // Each row: which link + which side is the clicked ("near") device.
  type Row = { lk: TopologyLink; near: "a" | "b" };
  const rows: Row[] =
    detail.kind === "device"
      ? links.flatMap((lk): Row[] =>
          lk.a_node_id === detail.id ? [{ lk, near: "a" }]
          : lk.b_node_id === detail.id ? [{ lk, near: "b" }]
          : [])
      : links.filter((lk) => lk.id === detail.id).map((lk): Row => ({ lk, near: "a" }));

  const title = detail.kind === "device"
    ? `Connections — ${nameOf(detail.id)}`
    : "Cable detail";
  const dot = (oper: string | null, support: boolean) =>
    support ? "#a1a1aa" : oper === "up" ? "#10b981" : oper === "down" ? "#ef4444" : "#a1a1aa";

  return (
    <div className="mt-3 rounded-md border border-border">
      <div className="flex items-center justify-between px-3 py-2 border-b border-border">
        <span className="text-sm font-semibold">🔌 {title}</span>
        <div className="flex items-center gap-3">
          {/* Open the clicked device's full detail (the old Devices-tab view). */}
          {detail.kind === "device" && nodeById[detail.id]?.source_ip ? (
            <button onClick={() => onOpenDevice(nodeById[detail.id].source_ip as string)}
              className="text-xs text-primary hover:underline whitespace-nowrap" title="Open full device detail">
              ↗ Open device detail
            </button>
          ) : null}
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground" title="Close">✕</button>
        </div>
      </div>
      {rows.length === 0 ? (
        <div className="px-3 py-4 text-xs text-muted-foreground">No cables connected to this device on the canvas.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-muted-foreground border-b border-border">
              <tr className="text-left [&>th]:px-3 [&>th]:py-1.5 [&>th]:font-medium">
                <th>Device A</th><th>Interface</th><th>Status</th><th>Traffic (↓IN / ↑OUT)</th>
                <th>Connected device</th><th>Interface</th><th>Status</th><th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ lk, near }) => {
                const a = linkEnd(lk, near), b = linkEnd(lk, near === "a" ? "b" : "a");
                const nA = nodeById[a.nodeId], nB = nodeById[b.nodeId];
                const aSup = !!nA?.support, bSup = !!nB?.support;
                // Support (icon-only) ends have no interface, no oper state and no data —
                // those cells read "—" rather than a slate dot or the other end's traffic.
                const statusCell = (oper: string | null, support: boolean) =>
                  support ? <span className="text-muted-foreground">—</span>
                  : <span className="inline-block h-2.5 w-2.5 rounded-full align-middle"
                      style={{ background: dot(oper, false) }} title={oper ?? "unknown"} />;
                return (
                  <tr key={lk.id} className="border-b border-border/50 last:border-0 [&>td]:px-3 [&>td]:py-1.5 align-middle">
                    <td className="font-medium">{nameOf(a.nodeId)}</td>
                    <td className="tabular-nums">{a.iff || "—"}</td>
                    <td>{statusCell(a.oper, aSup)}</td>
                    <td className="whitespace-nowrap">
                      {aSup ? <span className="text-muted-foreground">—</span> : (
                        <>
                          <span className="text-blue-600 dark:text-blue-400">↓ {fmtRate(a.rx ?? lk.rx_mbps)}</span>
                          {" / "}
                          <span className="text-orange-600 dark:text-orange-400">↑ {fmtRate(a.tx ?? lk.tx_mbps)}</span>
                        </>
                      )}
                    </td>
                    <td className="font-medium">{nameOf(b.nodeId)}</td>
                    <td className="tabular-nums">{b.iff || "—"}</td>
                    <td>{statusCell(b.oper, bSup)}</td>
                    <td>{nB?.source_ip ? (
                      <button onClick={() => onOpenDevice(nB.source_ip as string)}
                        className="text-primary hover:underline whitespace-nowrap" title="Open full device detail">↗ Open</button>
                    ) : null}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="bg-background rounded-lg border border-border w-full max-w-md p-4" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-sm">{title}</h3>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}

// Dropdown of built-in default icons with a live preview of the selection.
function DefaultIconPicker({ value, onChange }: { value: string; onChange: (k: string) => void }) {
  return (
    <div className="flex items-center gap-2 mt-1">
      <DeviceIcon kind={value} className="h-7 w-7 shrink-0" />
      <select value={value} onChange={(e) => onChange(e.target.value)}
        className="flex-1 px-2 py-1.5 text-sm rounded-md border border-border bg-background">
        {DEFAULT_ICONS.map((o) => <option key={o.kind} value={o.kind}>{o.label}</option>)}
      </select>
    </div>
  );
}

function AddDeviceModal({ placed, onClose, onAdd }: {
  placed: Set<string>; onClose: () => void; onAdd: (n: DraftNode) => void;
}) {
  const { data } = useSWR<Envelope<DeviceRow[]>>("/api/v1/network-devices/devices", swrFetcher);
  const [mode, setMode] = useState<"data" | "support">("data");
  const [sel, setSel] = useState("");
  const [name, setName] = useState("");
  const [icon, setIcon] = useState<IconData | null>(null); // chosen uploaded icon, or null = default
  const [kind, setKind] = useState("sw_l2");               // built-in default icon type
  const devices = data?.data || [];

  // Sensible default icon type: FortiGate → Firewall, other data → Switch L2, support → Cloud.
  useEffect(() => {
    setKind(mode === "support" ? "cloud" : devices.find((x) => x.source_ip === sel)?.vendor === "fortigate" ? "fw" : "sw_l2");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mode, sel]);

  const addData = () => {
    const d = devices.find((x) => x.source_ip === sel);
    if (!d) return;
    onAdd({ id: newId(), source_ip: d.source_ip, support: false, display_name: d.name,
      icon_kind: icon ? "custom" : kind,
      icon_id: icon?.id ?? null, icon_url: icon?.url ?? null, x: 40, y: 40 });
  };
  const addSupport = () => {
    if (!name.trim()) return;
    onAdd({ id: newId(), source_ip: null, support: true, display_name: name.trim(),
      icon_kind: icon ? "custom" : kind, icon_id: icon?.id ?? null, icon_url: icon?.url ?? null, x: 40, y: 40 });
  };

  return (
    <Modal title="Add Device to Topology" onClose={onClose}>
      <div className="flex gap-3 mb-3 text-xs">
        <label className="flex items-center gap-1"><input type="radio" checked={mode === "data"} onChange={() => setMode("data")} /> From data</label>
        <label className="flex items-center gap-1"><input type="radio" checked={mode === "support"} onChange={() => setMode("support")} /> Icon only (support)</label>
      </div>
      {mode === "data" ? (
        <>
          <select value={sel} onChange={(e) => setSel(e.target.value)} className="w-full px-2 py-1.5 text-sm rounded-md border border-border bg-background">
            <option value="">Select a monitored device…</option>
            {devices.map((d) => (
              // Not disabled when already placed — the same device may be added more than once.
              <option key={d.source_ip} value={d.source_ip}>
                {d.site} · {d.name} ({d.source_ip}){placed.has(d.source_ip) ? " — already on map" : ""}
              </option>
            ))}
          </select>
          <div className="mt-3">
            <span className="text-xs text-muted-foreground">Default icon</span>
            <DefaultIconPicker value={kind} onChange={setKind} />
          </div>
          <div className="mt-3">
            <span className="text-xs text-muted-foreground">Or upload custom icon</span>
            <IconPicker selectedId={icon?.id ?? null} onSelect={setIcon} />
          </div>
          <div className="mt-3 flex justify-end"><ToolbarBtn primary onClick={addData}>Add to Canvas</ToolbarBtn></div>
        </>
      ) : (
        <>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name (e.g. INTERNET)"
            className="w-full px-2 py-1.5 text-sm rounded-md border border-border bg-background" />
          <p className="text-[11px] text-muted-foreground mt-1">Icon-only node — no SNMP data, never shows green/red.</p>
          <div className="mt-3">
            <span className="text-xs text-muted-foreground">Default icon</span>
            <DefaultIconPicker value={kind} onChange={setKind} />
          </div>
          <div className="mt-3">
            <span className="text-xs text-muted-foreground">Or upload custom icon</span>
            <IconPicker selectedId={icon?.id ?? null} onSelect={setIcon} />
          </div>
          <div className="mt-3 flex justify-end"><ToolbarBtn primary onClick={addSupport}>Add to Canvas</ToolbarBtn></div>
        </>
      )}
    </Modal>
  );
}

// Enter the text first, then add the annotation node to the canvas.
function TextModal({ onClose, onAdd }: { onClose: () => void; onAdd: (text: string, rotation: number) => void }) {
  const [text, setText] = useState("");
  const [rotation, setRotation] = useState(0);
  const add = () => { const t = text.trim(); if (t) onAdd(t, rotation); };
  return (
    <Modal title="Add Text" onClose={onClose}>
      <label className="text-xs text-muted-foreground">Text</label>
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} autoFocus
        placeholder="Annotation text (e.g. “Core Ring”, “To ISP”)"
        className="w-full px-2 py-1.5 text-sm rounded-md border border-border bg-background mt-1" />
      <RotationField value={rotation} onChange={setRotation} />
      <p className="text-[11px] text-muted-foreground mt-1">Free-text label — drag it anywhere on the canvas after adding.</p>
      <div className="mt-3 flex justify-end"><ToolbarBtn primary onClick={add} disabled={!text.trim()}>Add to Canvas</ToolbarBtn></div>
    </Modal>
  );
}

// Rotation picker (degrees clockwise) — slider + number, shared by Add/Edit Text.
function RotationField({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  const clamp = (v: number) => ((Math.round(v) % 360) + 360) % 360;
  return (
    <div className="mt-3">
      <span className="text-xs text-muted-foreground">Rotation: {value}°</span>
      <div className="flex items-center gap-2 mt-1">
        <input type="range" min={0} max={359} step={1} value={value}
          onChange={(e) => onChange(clamp(Number(e.target.value)))} className="flex-1" />
        <input type="number" min={0} max={359} value={value}
          onChange={(e) => onChange(clamp(Number(e.target.value)))}
          className="w-16 px-2 py-1 text-sm rounded-md border border-border bg-background" />
      </div>
    </div>
  );
}

// Create a link, or edit an existing one (initial), or start a link from a fixed node (lockA).
function LinkModal({ nodes, initial, lockA, onClose, onSave }: {
  nodes: DraftNode[]; initial?: DraftLink; lockA?: string; onClose: () => void; onSave: (l: DraftLink) => void;
}) {
  const [aId, setAId] = useState(initial?.a_node_id || lockA || "");
  const [aIf, setAIf] = useState(initial?.a_if_name || "");
  const [bId, setBId] = useState(initial?.b_node_id || "");
  const [bIf, setBIf] = useState(initial?.b_if_name || "");
  const [type, setType] = useState<"lan" | "wan" | "mpls" | "wireless">(initial?.link_type || "lan");
  const [color, setColor] = useState<string | null>(initial?.color ?? null);

  const aNode = nodes.find((n) => n.id === aId);
  const bNode = nodes.find((n) => n.id === bId);
  const aIfaces = useDeviceIfaces(aNode?.support ? null : aNode?.source_ip || null);
  const bIfaces = useDeviceIfaces(bNode?.support ? null : bNode?.source_ip || null);
  const editing = !!initial;

  const save = () => {
    if (!aId || !bId || aId === bId) return;
    onSave({
      id: initial?.id || newId(), a_node_id: aId, a_if_name: aNode?.support ? null : aIf || null,
      b_node_id: bId, b_if_name: bNode?.support ? null : bIf || null, link_type: type,
      color: color,
      a_oper: initial?.a_oper ?? null, b_oper: initial?.b_oper ?? null,
      rx_mbps: initial?.rx_mbps ?? 0, tx_mbps: initial?.tx_mbps ?? 0,
    });
  };

  return (
    <Modal title={editing ? "Edit Link" : "Create Link"} onClose={onClose}>
      <div className="space-y-2 text-sm">
        <EndPicker label="From" nodes={nodes} nodeId={aId} setNodeId={setAId} ifaces={aIfaces} iff={aIf} setIff={setAIf} support={!!aNode?.support} />
        <EndPicker label="To" nodes={nodes} nodeId={bId} setNodeId={setBId} ifaces={bIfaces} iff={bIf} setIff={setBIf} support={!!bNode?.support} />
        <div>
          <span className="text-xs text-muted-foreground">Link type</span>
          <div className="flex gap-3 mt-1 text-xs">
            {(["lan", "wan", "mpls", "wireless"] as const).map((t) => (
              <label key={t} className="flex items-center gap-1 uppercase">
                <input type="radio" checked={type === t} onChange={() => setType(t)} /> {t}
              </label>
            ))}
          </div>
        </div>
        <div>
          <span className="text-xs text-muted-foreground">Line colour</span>
          {/* Two ways to pick: preset swatches, or the custom colour well for any shade. */}
          <div className="flex items-center gap-2 mt-1 flex-wrap">
            {LINK_COLORS.map((c) => {
              const active = (color ?? null) === c.value;
              return (
                <button key={c.label} type="button" title={c.label} onClick={() => setColor(c.value)}
                  className={`h-7 w-7 rounded-full border transition hover:scale-110 ${
                    active ? "ring-2 ring-foreground ring-offset-2 ring-offset-background border-transparent" : "border-border"
                  }`}
                  style={{ background: c.swatch }} />
              );
            })}
            <span className="mx-1 h-6 w-px bg-border" aria-hidden />
            <input type="color" value={color || "#64748b"} onChange={(e) => setColor(e.target.value)}
              title="Custom colour" className="h-7 w-10 rounded border border-border bg-background p-0.5" />
            <span className="text-xs tabular-nums text-muted-foreground">{color || "default"}</span>
          </div>
        </div>
      </div>
      <div className="mt-3 flex justify-end"><ToolbarBtn primary onClick={save}>{editing ? "Save Link" : "Create Link"}</ToolbarBtn></div>
    </Modal>
  );
}

// Edit a node's label + icon (display_name overrides SNMP; support nodes are identified by name).
function EditDeviceModal({ node, onClose, onSave }: {
  node: DraftNode; onClose: () => void; onSave: (patch: Partial<DraftNode>) => void;
}) {
  const [name, setName] = useState(node.display_name || "");
  const isText = node.icon_kind === "text";
  const isCustom = node.icon_kind === "custom";
  const [icon, setIcon] = useState<IconData | null>(
    isCustom && node.icon_url ? { id: node.icon_id || "", name: "", url: node.icon_url } : null,
  );
  // Built-in default type: seed from the node unless it was a custom upload.
  const [kind, setKind] = useState(isCustom ? "sw_l2" : node.icon_kind);
  const [rotation, setRotation] = useState(node.rotation || 0);
  const save = () => {
    if (isText) {
      onSave({ display_name: name.trim() || "Text", rotation });  // text node: text + rotation
      return;
    }
    onSave({
      display_name: name.trim() || node.display_name,
      // A picked custom icon wins; otherwise store the chosen default type.
      ...(icon
        ? { icon_kind: "custom", icon_id: icon.id, icon_url: icon.url }
        : { icon_kind: kind, icon_id: null, icon_url: null }),
    });
  };
  if (isText) {
    return (
      <Modal title="Edit Text" onClose={onClose}>
        <label className="text-xs text-muted-foreground">Text</label>
        <textarea value={name} onChange={(e) => setName(e.target.value)} rows={3}
          placeholder="Annotation text"
          className="w-full px-2 py-1.5 text-sm rounded-md border border-border bg-background mt-1" />
        <RotationField value={rotation} onChange={setRotation} />
        <p className="text-[11px] text-muted-foreground mt-1">Free-text label — place it anywhere on the canvas.</p>
        <div className="mt-3 flex justify-end"><ToolbarBtn primary onClick={save}>Save</ToolbarBtn></div>
      </Modal>
    );
  }
  return (
    <Modal title="Edit Device" onClose={onClose}>
      <label className="text-xs text-muted-foreground">Display name</label>
      <input value={name} onChange={(e) => setName(e.target.value)}
        placeholder={node.source_ip || "Name"}
        className="w-full px-2 py-1.5 text-sm rounded-md border border-border bg-background mt-1" />
      {node.source_ip ? (
        <p className="text-[11px] text-muted-foreground mt-1">Monitored device {node.source_ip} — the name is a display override.</p>
      ) : (
        <p className="text-[11px] text-muted-foreground mt-1">Icon-only (support) node — no SNMP data.</p>
      )}
      <div className="mt-3">
        <span className="text-xs text-muted-foreground">Default icon</span>
        <DefaultIconPicker value={kind} onChange={(k) => { setKind(k); setIcon(null); }} />
      </div>
      <div className="mt-3">
        <span className="text-xs text-muted-foreground">Or upload custom icon</span>
        <IconPicker selectedId={icon?.id ?? null} onSelect={setIcon} />
      </div>
      <div className="mt-3 flex justify-end"><ToolbarBtn primary onClick={save}>Save</ToolbarBtn></div>
    </Modal>
  );
}

// Manage the links attached to one node: edit / delete each, or add a new one from here.
function EditLinksModal({ nodeId, nodes, links, onClose, onEditLink, onNewLink, onDeleteLink }: {
  nodeId: string; nodes: DraftNode[]; links: DraftLink[]; onClose: () => void;
  onEditLink: (l: DraftLink) => void; onNewLink: (nodeId: string) => void; onDeleteLink: (id: string) => void;
}) {
  const [rows, setRows] = useState<DraftLink[]>(links.filter((l) => l.a_node_id === nodeId || l.b_node_id === nodeId));
  const nameOf = (id: string, iff: string | null) => {
    const n = nodes.find((x) => x.id === id);
    return `${n?.display_name || n?.source_ip || "?"}${iff ? ` · ${iff}` : ""}`;
  };
  const del = (id: string) => { onDeleteLink(id); setRows((p) => p.filter((l) => l.id !== id)); };

  return (
    <Modal title="Edit Links" onClose={onClose}>
      {rows.length === 0 ? (
        <p className="text-xs text-muted-foreground">No links on this device yet.</p>
      ) : (
        <ul className="space-y-1.5">
          {rows.map((l) => (
            <li key={l.id} className="flex items-center justify-between gap-2 text-xs rounded-md border border-border px-2 py-1.5">
              <span className="truncate">
                <span className="uppercase text-muted-foreground mr-1">{l.link_type}</span>
                {nameOf(l.a_node_id, l.a_if_name)} — {nameOf(l.b_node_id, l.b_if_name)}
              </span>
              <span className="flex gap-2 shrink-0">
                <button onClick={() => onEditLink(l)} className="hover:underline">Edit</button>
                <button onClick={() => del(l.id)} className="text-red-500 hover:underline">Delete</button>
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-3 flex justify-end"><ToolbarBtn primary onClick={() => onNewLink(nodeId)}>+ New link from here</ToolbarBtn></div>
    </Modal>
  );
}

function EndPicker({ label, nodes, nodeId, setNodeId, ifaces, iff, setIff, support }: {
  label: string; nodes: DraftNode[]; nodeId: string; setNodeId: (v: string) => void;
  ifaces: string[]; iff: string; setIff: (v: string) => void; support: boolean;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs w-10 text-muted-foreground">{label}</span>
      <select value={nodeId} onChange={(e) => setNodeId(e.target.value)} className="flex-1 px-2 py-1 text-xs rounded-md border border-border bg-background">
        <option value="">Node…</option>
        {nodes.map((n) => <option key={n.id} value={n.id}>{n.display_name || n.source_ip}</option>)}
      </select>
      {support ? (
        <span className="text-xs text-muted-foreground w-28">support (no iface)</span>
      ) : (
        <select value={iff} onChange={(e) => setIff(e.target.value)} className="w-28 px-2 py-1 text-xs rounded-md border border-border bg-background">
          <option value="">Iface…</option>
          {ifaces.map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
      )}
    </div>
  );
}

function useDeviceIfaces(source: string | null): string[] {
  const { data } = useSWR<Envelope<{ interfaces: { if_name: string }[] }>>(
    source ? `/api/v1/network-devices/devices/${encodeURIComponent(source)}/interfaces` : null,
    swrFetcher,
  );
  return (data?.data.interfaces || []).map((i) => i.if_name);
}
