import type { MyTokenUsage } from "@/api/types";
import { cn } from "@/lib/utils";

// ─── 헤더 사용량 표시 ───
// 한도는 토큰이 아니라 비용(달러)으로 집행된다(clients/llm/quota_gate). 헤더에는 그
// 집행 단위만 둔다 - 토큰 수는 한도와 무관해서 헤더에서 판단에 쓰이지 않는다
// (사용 내역은 마이페이지에서 입력·출력·모델별로 본다).
//
// 헤더는 본작업이 아니다. 테두리·배경을 두르면 그만큼 시선을 가져가므로 글자만 놓고,
// 남은 여유는 비용 밑 얇은 선으로만 알린다. 라벨은 흐리게 수치는 진하게 둬서
// 훑을 때 숫자가 먼저 들어오게 한다. 정확한 값과 기간은 마우스를 올리면 나온다.

/** 한도에 가까울수록 눈에 띄게 - 80%부터 주의, 95%부터 경고. */
function toneOf(ratio: number): { bar: string; value: string } {
  if (ratio >= 0.95) return { bar: "bg-fg-danger", value: "text-fg-danger" };
  if (ratio >= 0.8) return { bar: "bg-fg-warning", value: "text-fg-warning" };
  return { bar: "bg-accent", value: "text-fg" };
}

export function UsageMeter({ usage }: { usage: MyTokenUsage }) {
  const limit = usage.cost_limit_usd;
  const ratio = limit && limit > 0 ? Math.min(usage.total_cost_usd / limit, 1) : 0;
  const tone = toneOf(ratio);
  const period = `${usage.period_start} ~ ${usage.period_end}`;

  return (
    // tabular-nums: 값이 바뀔 때 자리폭이 흔들리지 않게(헤더가 미세하게 들썩였다)
    <div className="flex items-center text-xs tabular-nums">
      <span
        className="flex flex-col gap-1"
        title={
          limit
            ? `이번 달 비용 $${usage.total_cost_usd.toFixed(2)} / 한도 $${limit.toFixed(2)} (${Math.round(ratio * 100)}%)\n${period}`
            : `이번 달 비용 $${usage.total_cost_usd.toFixed(2)} (${period})`
        }
      >
        <span className="flex items-baseline gap-1.5">
          <span className="text-fg-tertiary">비용</span>
          <span className={cn("font-medium", tone.value)}>${usage.total_cost_usd.toFixed(2)}</span>
          {limit ? <span className="text-fg-tertiary">/ ${limit.toFixed(0)}</span> : null}
        </span>
        {limit ? (
          // 밑줄 자리의 잔량선 - 숫자를 읽지 않아도 여유가 보인다.
          <span className="block h-[3px] w-full overflow-hidden rounded-full bg-border" aria-hidden>
            <span
              className={cn("block h-full rounded-full transition-[width]", tone.bar)}
              style={{ width: `${Math.max(ratio * 100, 2)}%` }}
            />
          </span>
        ) : null}
      </span>
    </div>
  );
}
