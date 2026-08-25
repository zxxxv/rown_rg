import { Bot, RotateCcw, User } from "lucide-react";
import { cn } from "@/lib/utils";

// 진행 단계 지도(2026-08-20 사용자 요청) - 파이프라인이 어떤 순서로 돌고, 어디서
// 사람의 검토가 필요한지, 어디로 되돌아올 수 있는지를 설계 화면에서 한눈에 보여준다.
// 사람 단계는 아이콘·색으로 구분한다 - "여기가 사람 검토 지점이다"가 지도의 목적.

interface Stage {
  key: string;
  label: string;
  human?: boolean;
}

const STAGES: Stage[] = [
  { key: "planning", label: "계획" },
  { key: "brief", label: "설계 검토", human: true },
  { key: "research", label: "자료 수집" },
  { key: "sources", label: "자료 검토", human: true },
  { key: "indexing", label: "색인·리허설" },
  { key: "writing", label: "본문 작성" },
  { key: "review", label: "검증·조립" },
  { key: "done", label: "완성 검토", human: true },
];

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
