import { Coins, Zap } from "lucide-react";
import type { MyTokenUsage } from "@/api/types";
import { cn } from "@/lib/utils";

// ─── 헤더 사용량 표시 ───
// 한도는 토큰이 아니라 비용(달러)으로 집행된다(clients/llm/quota_gate). 그래서 토큰은
// 사용량만, 비용은 한도 대비로 보여준다 - 없는 상한을 지어내지 않는다.
//
// 숫자만 나열하면 "많은 건지 적은 건지"를 읽는 데 매번 계산이 필요하다. 남은 여유를
// 막대와 색으로 먼저 보이게 하고, 정확한 값은 마우스를 올리면 나온다.

/** 17,923,918 → "17.9M". 헤더는 폭이 귀하고, 정확한 값은 툴팁에 있다. */
function compactTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1_000)}K`;
  return n.toLocaleString();
}

/** 한도에 가까울수록 눈에 띄게 - 80%부터 주의, 95%부터 경고. */
function toneOf(ratio: number): { bar: string; text: string; ring: string } {
  if (ratio >= 0.95) return { bar: "bg-fg-danger", text: "text-fg-danger", ring: "bg-bg-danger" };
  if (ratio >= 0.8) return { bar: "bg-fg-warning", text: "text-fg-warning", ring: "bg-bg-warning" };
  return { bar: "bg-accent", text: "text-fg", ring: "bg-bg-secondary" };
}

export function UsageMeter({ usage }: { usage: MyTokenUsage }) {
  const tokens = usage.total_input_tokens + usage.total_output_tokens;
  const limit = usage.cost_limit_usd;
  const ratio = limit && limit > 0 ? Math.min(usage.total_cost_usd / limit, 1) : 0;
  const tone = toneOf(ratio);
  const period = `${usage.period_start} ~ ${usage.period_end}`;

  return (
    <div className="flex items-center gap-1.5">
      {/* 토큰: 상한이 없는 값이라 막대 없이 수치만. 좁은 화면에서는 접는다. */}
      <div
        className="hidden items-center gap-1.5 rounded-full border border-border bg-bg-secondary px-2.5 py-1 sm:flex"
        title={`이번 달 토큰 ${tokens.toLocaleString()} (${period})\n입력 ${usage.total_input_tokens.toLocaleString()} / 출력 ${usage.total_output_tokens.toLocaleString()}`}
      >
        <Zap className="h-3.5 w-3.5 text-fg-tertiary" aria-hidden />
        <span className="font-mono text-xs text-fg">{compactTokens(tokens)}</span>
        <span className="text-[11px] text-fg-tertiary">토큰</span>
      </div>

      {/* 비용: 실제로 집행되는 한도라 남은 여유를 막대로 보인다. */}
      <div
        className={cn(
          "flex items-center gap-2 rounded-full border border-border px-2.5 py-1",
          ratio >= 0.8 ? tone.ring : "bg-bg-secondary",
        )}
        title={
          limit
            ? `이번 달 비용 $${usage.total_cost_usd.toFixed(2)} / 한도 $${limit.toFixed(2)} (${Math.round(ratio * 100)}%)\n${period}`
            : `이번 달 비용 $${usage.total_cost_usd.toFixed(2)} (${period})`
        }
      >
        <Coins className={cn("h-3.5 w-3.5", ratio >= 0.8 ? tone.text : "text-fg-tertiary")} />
        <span className="font-mono text-xs text-fg">${usage.total_cost_usd.toFixed(2)}</span>
        {limit ? (
          <>
            <span className="font-mono text-[11px] text-fg-tertiary">/ ${limit.toFixed(0)}</span>
            <span
              className="hidden h-1.5 w-16 overflow-hidden rounded-full bg-border md:block"
              aria-hidden
            >
              <span
                className={cn("block h-full rounded-full transition-[width]", tone.bar)}
                style={{ width: `${Math.max(ratio * 100, 2)}%` }}
              />
            </span>
          </>
        ) : null}
      </div>
    </div>
  );
}
