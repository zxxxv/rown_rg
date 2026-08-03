import { cn } from "@/lib/utils";

// 실측 누적만 표시한다 — 이전의 "/ 예상치" 분모는 estimator의 가짜 근사여서
// 실비용을 왜곡했다(예: $0.23 실측 옆에 $115 추정). 비용은 소수 2자리로.
export interface CostTrackerProps {
  tokensUsed: number;
  costUsed: number;
  className?: string;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}K`;
  return `${n}`;
}

export function CostTracker({ tokensUsed, costUsed, className }: CostTrackerProps) {
  return (
    <div className={cn("flex flex-col gap-3 rounded border border-border bg-bg p-4", className)}>
      <p className="text-xs font-medium text-fg-secondary">누적 사용량 (실측)</p>
      <Row label="토큰" value={formatTokens(tokensUsed)} />
      <Row label="비용" value={`$${costUsed.toFixed(2)}`} />
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2 text-xs">
      <span className="text-fg-tertiary">{label}</span>
      <span className="font-mono text-sm text-fg">{value}</span>
    </div>
  );
}
