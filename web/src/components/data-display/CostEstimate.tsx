import { cn } from "@/lib/utils";

export interface CostEstimateProps {
  estimatedHours: number;
  estimatedTokens: number;
  estimatedCostUsd: number;
  className?: string;
}

function formatHours(h: number): string {
  if (h < 1) return `${Math.round(h * 60)}분`;
  if (Number.isInteger(h)) return `${h}시간`;
  return `${h.toFixed(1)}시간`;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return `${n}`;
}

function formatUsd(n: number): string {
  return `$${n.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

export function CostEstimate({
  estimatedHours,
  estimatedTokens,
  estimatedCostUsd,
  className,
}: CostEstimateProps) {
  return (
    <dl
      className={cn(
        "grid grid-cols-3 gap-3 rounded border border-border bg-bg-secondary p-3",
        className,
      )}
    >
      <Item label="예상 시간" value={formatHours(estimatedHours)} />
      <Item label="예상 토큰" value={formatTokens(estimatedTokens)} />
      <Item label="예상 비용" value={formatUsd(estimatedCostUsd)} />
    </dl>
  );
}

function Item({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-fg-tertiary">{label}</dt>
      <dd className="font-mono text-base font-medium text-fg">{value}</dd>
    </div>
  );
}
