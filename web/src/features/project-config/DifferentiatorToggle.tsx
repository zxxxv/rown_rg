import { Controller, useFormContext } from "react-hook-form";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { cn } from "@/lib/utils";
import type { ProjectFormValues } from "./schema";

type DiffName =
  | "config.enable_pre_reconciliation"
  | "config.enable_consistency_graph"
  | "config.enable_dual_track_search"
  | "config.enable_source_tagging"
  | "config.enable_critic_agent"
  | "config.enable_glossary";

interface DiffOption {
  name: DiffName;
  id: string;
  title: string;
  description: string;
  cost?: string;
  /** 백엔드 미구현 — 켜도 아직 파이프라인에 반영되지 않는 기능 */
  upcoming?: boolean;
}

const OPTIONS: DiffOption[] = [
  {
    name: "config.enable_pre_reconciliation",
    id: "diff-pre-rec",
    title: "자료 모순 사전 검증",
    description: "작성 전 자료 간 모순을 발견해 사용자 결정으로 해소.",
    cost: "+5분",
    upcoming: true,
  },
  {
    name: "config.enable_consistency_graph",
    id: "diff-consist",
    title: "일관성 그래프",
    description: "섹션 간 주장·수치의 일관성을 그래프로 시각화.",
    upcoming: true,
  },
  {
    name: "config.enable_dual_track_search",
    id: "diff-dual",
    title: "듀얼 트랙 검색",
    description: "회사 양식·관행을 반영한 이중 트랙 검색.",
    upcoming: true,
  },
  {
    name: "config.enable_source_tagging",
    id: "diff-tag",
    title: "출처 태깅 자동",
    description: "본문 내 주장마다 자동 출처 태그 부착.",
  },
  {
    name: "config.enable_critic_agent",
    id: "diff-critic",
    title: "Critic Agent",
    description: "외부 검토자 시뮬 — 일관성 그래프와 함께 동작.",
    cost: "+10분, +$5",
  },
  {
    name: "config.enable_glossary",
    id: "diff-glossary",
    title: "자동 약어·용어집 생성",
    description: "보고서 마지막에 자동 부록 추가.",
  },
];

export function DifferentiatorToggle() {
  const { control } = useFormContext<ProjectFormValues>();

  return (
    <div className="flex flex-col gap-2">
      {OPTIONS.map((opt) => (
        <Controller
          key={opt.name}
          name={opt.name}
          control={control}
          render={({ field }) => (
            <div
              className={cn(
                "flex items-start justify-between gap-3 rounded border p-3 transition-colors",
                field.value ? "border-accent/40 bg-bg-info" : "border-border bg-bg",
              )}
            >
              <div className="flex flex-col gap-0.5">
                <div className="flex items-center gap-2">
                  <Label htmlFor={opt.id} className="cursor-pointer text-sm font-medium text-fg">
                    {opt.title}
                  </Label>
                  {opt.upcoming ? (
                    <span className="rounded-sm border border-border bg-bg-secondary px-1.5 py-0.5 text-[10px] text-fg-tertiary">
                      준비중
                    </span>
                  ) : null}
                  {opt.cost ? (
                    <span className="font-mono text-xs text-fg-tertiary">{opt.cost}</span>
                  ) : null}
                </div>
                <p className="text-xs text-fg-tertiary">{opt.description}</p>
              </div>
              <Switch
                id={opt.id}
                checked={field.value}
                onCheckedChange={(v) => field.onChange(v === true)}
                aria-label={opt.title}
              />
            </div>
          )}
        />
      ))}
    </div>
  );
}
