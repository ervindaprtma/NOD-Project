"use client";

import { useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { apiFetch, swrFetcher, hasMinRole } from "@/lib/api";
import type { DeviceRow, Envelope, IconData, TopologyData, TopologyLink, TopologyNode } from "./types";
import { ICON_GLYPH, LINK_STYLE, fmtRate } from "./types";
import { IconPicker } from "./IconPicker";

const NODE_W = 120;
const NODE_H = 56;
const GRID = 20;

type DraftNode = Omit<TopologyNode, "online"> & { online?: boolean | null };
type DraftLink = TopologyLink;

const newId = () =>
  (crypto.randomUUID?.() || `${Date.now()}${Math.random()}`).replace(/-/g, "").slice(0, 32).padEnd(32, "0");
const snap = (v: number) => Math.round(v / GRID) * GRID;

export function TopologyCanvas({ onOpenDevice, refreshMs = 30_000 }: { onOpenDevice: (source: string) => void; refreshMs?: number }) {
  const { data, error, isLoading, mutate } = useSWR<Envelope<TopologyData>>(
    "/api/v1/topology",
    swrFetcher,
    { refreshInterval: refreshMs },
  );
  const canEdit = hasMinRole("superadmin");
  const [editing, setEditing] = useState(false);
  const [draftNodes, setDraftNodes] = useState<DraftNode[]>([]);
  const [draftLinks, setDraftLinks] = useState<DraftLink[]>([]);
  const [activeLink, setActiveLink] = useState<string | null>(null);
  const [modal, setModal] = useState<null | "device">(null);
  const [saving, setSaving] = useState(false);
  // Right-click context menu + edit flows (edit mode only).
  const [ctx, setCtx] = useState<{ x: number; y: number; nodeId: string | null } | null>(null);
  const [editNode, setEditNode] = useState<DraftNode | null>(null);   // Edit Device modal
  const [linksForNode, setLinksForNode] = useState<string | null>(null); // Edit Links manager (node id)
  const [linkEdit, setLinkEdit] = useState<null | { initial?: DraftLink; lockA?: string }>(null); // LinkModal

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

  const startEdit = () => {
    setDraftNodes((topo?.nodes || []).map((n) => ({ ...n })));
    setDraftLinks((topo?.links || []).map((l) => ({ ...l })));
    setEditing(true);
  };
  const cancelEdit = () => {
    setEditing(false);
    setModal(null);
  };
  const save = async () => {
    setSaving(true);
    try {
      await apiFetch("/api/v1/topology", {
        method: "PUT",
        body: JSON.stringify({
          nodes: draftNodes.map((n) => ({
            id: n.id, source_ip: n.source_ip, support: n.support,
            display_name: n.display_name, icon_kind: n.icon_kind, icon_id: n.icon_id, x: n.x, y: n.y,
          })),
          links: draftLinks.map((l) => ({
            id: l.id, a_node_id: l.a_node_id, a_if_name: l.a_if_name,
            b_node_id: l.b_node_id, b_if_name: l.b_if_name, link_type: l.link_type,
          })),
        }),
      });
      setEditing(false);
      await mutate();
    } catch {
      alert("Save failed.");
    } finally {
      setSaving(false);
    }
  };

  // ── drag (edit mode only) ──
  const dragRef = useRef<{ id: string; dx: number; dy: number } | null>(null);
  const onNodePointerDown = (e: React.PointerEvent, n: DraftNode) => {
    if (!editing) return;
    e.preventDefault();
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
    dragRef.current = { id: n.id, dx: e.clientX - n.x, dy: e.clientY - n.y };
  };
  const onCanvasPointerMove = (e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    setDraftNodes((prev) =>
      prev.map((n) => (n.id === d.id ? { ...n, x: Math.max(0, snap(e.clientX - d.dx)), y: Math.max(0, snap(e.clientY - d.dy)) } : n)),
    );
  };
  const onCanvasPointerUp = () => { dragRef.current = null; };

  const { w, h } = useMemo(() => {
    let mw = 600, mh = 400;
    for (const n of nodes) { mw = Math.max(mw, n.x + NODE_W + 40); mh = Math.max(mh, n.y + NODE_H + 40); }
    return { w: mw, h: mh };
  }, [nodes]);

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
      <div className="flex items-center justify-between mb-2">
        <Legend />
        {canEdit ? (
          editing ? (
            <div className="flex gap-2">
              <ToolbarBtn onClick={() => setModal("device")}>+ Device</ToolbarBtn>
              <ToolbarBtn onClick={() => setLinkEdit({})}>+ Link</ToolbarBtn>
              <ToolbarBtn onClick={save} disabled={saving} primary>{saving ? "Saving…" : "Save"}</ToolbarBtn>
              <ToolbarBtn onClick={cancelEdit}>Cancel</ToolbarBtn>
            </div>
          ) : (
            <ToolbarBtn onClick={startEdit}>✎ Edit Layout</ToolbarBtn>
          )
        ) : null}
      </div>

      {editing ? <UnmappedBanner draftNodes={draftNodes} onAdd={(d) => addDeviceNode(d, setDraftNodes, draftNodes)} /> : null}

      {nodes.length === 0 && !editing ? (
        <div className="rounded-lg border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
          No topology yet. {canEdit ? "Click “Edit Layout” to add devices." : "A superadmin can add devices."}
        </div>
      ) : (
        <div
          className="relative overflow-auto rounded-lg border border-border bg-muted/10"
          style={{ minHeight: 420 }}
          onPointerMove={onCanvasPointerMove}
          onPointerUp={onCanvasPointerUp}
          onContextMenu={(e) => openCtx(e, null)}
        >
          <div className="relative" style={{ width: w, height: h }}>
            <svg className="absolute inset-0" width={w} height={h}>
              {links.map((lk) => {
                const a = nodeById[lk.a_node_id], b = nodeById[lk.b_node_id];
                if (!a || !b) return null;
                const ca = center(a), cb = center(b), style = LINK_STYLE[lk.link_type] || LINK_STYLE.lan;
                const ea = endPoint(a, b), eb = endPoint(b, a);
                const lkR = lk as TopologyLink;
                // Label anchors: nudge each end's rate inward (toward midpoint) and off the
                // line (perpendicular) so the ↓/↑ text isn't jammed against the node or the line.
                const vx = cb.cx - ca.cx, vy = cb.cy - ca.cy, vlen = Math.hypot(vx, vy) || 1;
                const ux = vx / vlen, uy = vy / vlen, nx = -uy, ny = ux;
                const IN_OFF = 22, PERP_OFF = 13;
                const aLbl = { x: ea.x + ux * IN_OFF + nx * PERP_OFF, y: ea.y + uy * IN_OFF + ny * PERP_OFF };
                const bLbl = { x: eb.x - ux * IN_OFF + nx * PERP_OFF, y: eb.y - uy * IN_OFF + ny * PERP_OFF };
                return (
                  <g key={lk.id} className="cursor-pointer" onClick={() => setActiveLink(activeLink === lk.id ? null : lk.id)}>
                    <line x1={ca.cx} y1={ca.cy} x2={cb.cx} y2={cb.cy} stroke="currentColor"
                      className="text-muted-foreground" strokeWidth={style.width} strokeDasharray={style.dash} />
                    <circle cx={ea.x} cy={ea.y} r={5} fill={dotColor(lkR.a_oper, a.support)}
                      stroke={a.support ? "#a1a1aa" : "none"} strokeDasharray={a.support ? "2 2" : undefined} />
                    <circle cx={eb.x} cy={eb.y} r={5} fill={dotColor(lkR.b_oper, b.support)}
                      stroke={b.support ? "#a1a1aa" : "none"} strokeDasharray={b.support ? "2 2" : undefined} />
                    {/* Per-interface speed near each data end: ↓ IN blue, ↑ OUT orange
                        (same colours as the Interface Bandwidth tab). Support ends have no rate. */}
                    {!editing && lkR.a_rx_mbps != null ? (
                      <LinkEndRate x={aLbl.x} y={aLbl.y} rx={lkR.a_rx_mbps} tx={lkR.a_tx_mbps ?? 0} />
                    ) : null}
                    {!editing && lkR.b_rx_mbps != null ? (
                      <LinkEndRate x={bLbl.x} y={bLbl.y} rx={lkR.b_rx_mbps} tx={lkR.b_tx_mbps ?? 0} />
                    ) : null}
                  </g>
                );
              })}
            </svg>

            {nodes.map((n) => (
              <div
                key={n.id}
                onPointerDown={(e) => onNodePointerDown(e, n as DraftNode)}
                onClick={() => !editing && n.source_ip && onOpenDevice(n.source_ip)}
                onContextMenu={(e) => openCtx(e, n.id)}
                title={n.display_name || n.source_ip || ""}
                className={`absolute flex flex-col items-center justify-center rounded-md border-2 bg-background text-center px-2 ${
                  n.support ? "border-zinc-400/50" : n.online ? "border-emerald-500" : "border-red-500"
                } ${editing ? "cursor-move" : n.source_ip ? "cursor-pointer hover:bg-muted/40" : ""}`}
                style={{ left: n.x, top: n.y, width: NODE_W, height: NODE_H, touchAction: "none" }}
              >
                {n.icon_url ? (
                  // eslint-disable-next-line @next/next/no-img-element -- SVG via <img> can't run script
                  <img src={n.icon_url} alt="" className="h-6 w-6 object-contain pointer-events-none" />
                ) : (
                  <span className="text-lg leading-none">{ICON_GLYPH[n.icon_kind] || "⬡"}</span>
                )}
                <span className="text-[11px] font-medium truncate w-full">{n.display_name || n.source_ip}</span>
                {editing ? (
                  <button
                    onClick={(e) => { e.stopPropagation(); deleteNode(n.id); }}
                    className="absolute -top-2 -right-2 w-4 h-4 rounded-full bg-red-500 text-white text-[10px] leading-none"
                    title="Remove from map"
                  >×</button>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      )}

      {activeLink ? (() => {
        const lk = links.find((l) => l.id === activeLink);
        if (!lk) return null;
        return <LinkPanel link={lk as TopologyLink} nodeById={nodeById} editing={editing}
          onClose={() => setActiveLink(null)} onDelete={() => { deleteLink(lk.id); setActiveLink(null); }} />;
      })() : null}

      {modal === "device" ? (
        <AddDeviceModal
          placed={new Set(draftNodes.map((n) => n.source_ip).filter(Boolean) as string[])}
          onClose={() => setModal(null)}
          onAdd={(node) => { setDraftNodes((p) => [...p, node]); setModal(null); }}
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
                ]
              : [
                  { label: "✎ Edit Device", on: () => setEditNode((draftNodes.find((n) => n.id === ctx.nodeId) as DraftNode) || null) },
                  { label: "🔗 Edit Link", on: () => setLinksForNode(ctx.nodeId) },
                  { label: "🗑 Delete Device", on: () => deleteNode(ctx.nodeId as string), danger: true },
                ]
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

function ToolbarBtn({ children, onClick, primary, disabled }: { children: React.ReactNode; onClick: () => void; primary?: boolean; disabled?: boolean }) {
  return (
    <button onClick={onClick} disabled={disabled}
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

function AddDeviceModal({ placed, onClose, onAdd }: {
  placed: Set<string>; onClose: () => void; onAdd: (n: DraftNode) => void;
}) {
  const { data } = useSWR<Envelope<DeviceRow[]>>("/api/v1/network-devices/devices", swrFetcher);
  const [mode, setMode] = useState<"data" | "support">("data");
  const [sel, setSel] = useState("");
  const [name, setName] = useState("");
  const [icon, setIcon] = useState<IconData | null>(null); // chosen uploaded icon, or null = default
  const devices = data?.data || [];

  const addData = () => {
    const d = devices.find((x) => x.source_ip === sel);
    if (!d) return;
    onAdd({ id: newId(), source_ip: d.source_ip, support: false, display_name: d.name,
      icon_kind: icon ? "custom" : d.vendor === "fortigate" ? "fg" : "sw",
      icon_id: icon?.id ?? null, icon_url: icon?.url ?? null, x: 40, y: 40 });
  };
  const addSupport = () => {
    if (!name.trim()) return;
    onAdd({ id: newId(), source_ip: null, support: true, display_name: name.trim(),
      icon_kind: icon ? "custom" : "cloud", icon_id: icon?.id ?? null, icon_url: icon?.url ?? null, x: 40, y: 40 });
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
              <option key={d.source_ip} value={d.source_ip} disabled={placed.has(d.source_ip)}>
                {d.site} · {d.name} ({d.source_ip}){placed.has(d.source_ip) ? " — on map" : ""}
              </option>
            ))}
          </select>
          <div className="mt-3">
            <span className="text-xs text-muted-foreground">Icon</span>
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
            <span className="text-xs text-muted-foreground">Icon</span>
            <IconPicker selectedId={icon?.id ?? null} onSelect={setIcon} />
          </div>
          <div className="mt-3 flex justify-end"><ToolbarBtn primary onClick={addSupport}>Add to Canvas</ToolbarBtn></div>
        </>
      )}
    </Modal>
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
  const [icon, setIcon] = useState<IconData | null>(
    node.icon_kind === "custom" && node.icon_url ? { id: node.icon_id || "", name: "", url: node.icon_url } : null,
  );
  const save = () => {
    onSave({
      display_name: name.trim() || node.display_name,
      // A newly-picked icon → custom; untouched → keep the node's existing icon.
      ...(icon
        ? { icon_kind: "custom", icon_id: icon.id, icon_url: icon.url }
        : { icon_kind: node.icon_kind, icon_id: node.icon_id, icon_url: node.icon_url }),
    });
  };
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
        <span className="text-xs text-muted-foreground">Icon</span>
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
