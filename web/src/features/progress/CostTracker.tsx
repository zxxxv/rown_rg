import { cn } from "@/lib/utils";

export interface CostTrackerProps {
  tokensUsed: number;
  tokensExpected: number;
  costUsed: number;
  costExpected: number;
  className?: string;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return `${n}`;
}

function pct(used: number, expected: number): number {
  if (expected <= 0) return 0;
  return Math.min(100, Math.max(0, (used / expected) * 100));
}

export function CostTracker({
  tokensUsed,
  tokensExpected,
  costUsed,
  costExpected,
  className,
}: CostTrackerProps) {
  const tokenPct = pct(tokensUsed, tokensExpected);
  const costPct = pct(costUsed, costExpected);

  return (
    <div className={cn("flex flex-col gap-3 rounded border border-border bg-bg p-4", className)}>
      <p className="text-xs font-medium text-fg-secondary">누적 비용</p>

      <Row
        label="토큰"
        used={formatTokens(tokensUsed)}
        expected={formatTokens(tokensExpected)}
        percent={tokenPct}
      />
      <Row
        label="비용"
        used={`$${costUsed.toFixed(0)}`}
        expected={`$${costExpected.toFixed(0)}`}
        percent={costPct}
      />
    </div>
  );
}

function Row({
  label,
  used,
  expected,
  percent,
}: {
  label: string;
  used: string;
  expected: string;
  percent: number;
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="text-fg-tertiary">{label}</span>
        <span className="font-mono text-fg">
          {used} <span className="text-fg-tertiary">/ {expected}</span>
        </span>
      </div>
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-bg-tertiary">
        <div className="h-full bg-accent transition-[width]" style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}
