import { Bot, RotateCcw, User } from "lucide-react";
import { PIPELINE_STAGES, type PipelineStageKey } from "@/features/progress/stages";
import { cn } from "@/lib/utils";

// 진행 단계 지도(2026-08-20 사용자 요청) - 파이프라인이 어떤 순서로 돌고, 어디서
// 사람의 검토가 필요한지, 어디로 되돌아올 수 있는지를 설계 화면에서 한눈에 보여준다.
// 사람 단계는 아이콘·색으로 구분한다 - "여기가 사람 검토 지점이다"가 지도의 목적.
// 키·라벨은 stages.ts 단일 진실에서 온다 - 이 지도만 라벨 어순이 뒤집혀
// ("검증·조립") 스테퍼와 어긋나 있었다. 옛 "계획" 칸·옛 키(planning 등)도
// 스테퍼의 단계 집합에 맞춰 걷어냈다.

const HUMAN_STAGES = new Set<PipelineStageKey>(["brief", "review", "done"]);

const STAGES = PIPELINE_STAGES.map((s) => ({ ...s, human: HUMAN_STAGES.has(s.key) }));

export function StageMap({ current }: { current?: string }) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border bg-bg-secondary p-3">
      <div className="flex flex-wrap items-center gap-x-1 gap-y-2">
        {STAGES.map((s, i) => (
          <div key={s.key} className="flex items-center gap-1">
            <span
              className={cn(
                "flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px]",
                s.human
                  ? "border-accent/50 bg-bg-info text-fg"
                  : "border-border bg-bg text-fg-secondary",
                current === s.key && "ring-1 ring-accent font-medium",
              )}
            >
              {s.human ? (
                <User className="h-3 w-3 text-accent" aria-label="사람 검토" />
              ) : (
                <Bot className="h-3 w-3 text-fg-tertiary" aria-label="자동" />
              )}
              {s.label}
            </span>
            {i < STAGES.length - 1 ? (
              <span aria-hidden className="text-fg-tertiary">
                ›
              </span>
            ) : null}
          </div>
        ))}
      </div>
      <div className="flex flex-col gap-0.5 text-[11px] text-fg-tertiary">
        <p className="flex items-center gap-1">
          <RotateCcw className="h-3 w-3" aria-hidden />
          리허설에서 자료가 부족한 절이 있으면 자료 검토로 되돌아옵니다(최대 2회).
        </p>
        <p className="flex items-center gap-1">
          <RotateCcw className="h-3 w-3" aria-hidden />
          완성 후 절 재작성·자료 보강은 전체를 되돌리지 않고 해당 절만 다시 씁니다.
        </p>
        <p className="flex items-center gap-1">
          <User className="h-3 w-3 text-accent" aria-hidden />
          표시가 있는 단계가 사람 검토 지점입니다. 나머지는 자동으로 진행됩니다.
        </p>
      </div>
    </div>
  );
}
