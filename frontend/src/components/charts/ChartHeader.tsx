"use client";

/** Chart title row with a drag-to-zoom hint and a Reset-zoom button (shown once zoomed). */
export function ChartHeader({
  title,
  isZoomed,
  onReset,
}: {
  title: string;
  isZoomed: boolean;
  onReset: () => void;
}) {
  return (
    <div className="flex items-center justify-between mb-3 gap-2">
      <h2 className="text-lg font-semibold">{title}</h2>
      <div className="flex items-center gap-2 shrink-0">
        <span className="hidden sm:inline text-[11px] text-muted-foreground">drag to zoom</span>
        {isZoomed && (
          <button
            type="button"
            onClick={onReset}
            className="inline-flex items-center gap-1 rounded-md border border-border/60 px-2 py-1 text-[11px] font-medium text-muted-foreground hover:bg-muted/50 transition-colors"
          >
            ⟲ Reset zoom
          </button>
        )}
      </div>
    </div>
  );
}
