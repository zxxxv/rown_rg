import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

export interface EtaIndicatorProps {
  startedAt: number;
  etaSeconds: number | null;
  className?: string;
}

function formatDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

export function EtaIndicator({ startedAt, etaSeconds, className }: EtaIndicatorProps) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  const elapsedMs = now - startedAt;
  const remainingMs = etaSeconds !== null ? etaSeconds * 1000 : null;

  return (
    <div className={cn("flex flex-col gap-2 rounded border border-border bg-bg p-4", className)}>
      <p className="text-xs font-medium text-fg-secondary">시간</p>
      <dl className="grid grid-cols-2 gap-3">
        <div className="flex flex-col">
          <dt className="text-xs text-fg-tertiary">경과</dt>
          <dd className="font-mono text-lg text-fg">{formatDuration(elapsedMs)}</dd>
        </div>
        <div className="flex flex-col">
          <dt className="text-xs text-fg-tertiary">남은 시간</dt>
          <dd className="font-mono text-lg text-fg">
            {remainingMs !== null ? formatDuration(remainingMs) : "—"}
          </dd>
        </div>
      </dl>
    </div>
  );
}
