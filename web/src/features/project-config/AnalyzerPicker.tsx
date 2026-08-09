import { Info } from "lucide-react";
import { Controller, useFormContext } from "react-hook-form";
import { type Analyzer, AnalyzerSchema } from "@/api/types";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { ProjectFormValues } from "./schema";

const ANALYZER_LABEL: Record<Analyzer, string> = {
  STEEP: "STEEP 분석",
  SWOT: "SWOT 분석",
  FIVE_FORCES: "5 Forces 분석",
  PESTLE: "PESTLE 분석",
  RISK: "Risk 분석",
  COST_BENEFIT: "Cost-Benefit 분석",
};

const ANALYZER_DESC: Record<Analyzer, string> = {
  STEEP: "사회·기술·경제·환경·정치 5축 거시환경 분석.",
  SWOT: "강점·약점·기회·위협 4분면 매트릭스 분석.",
  FIVE_FORCES: "산업의 5가지 경쟁 압력 진단 (Porter).",
  PESTLE: "정치·경제·사회·기술·법·환경 6축 분석.",
  RISK: "리스크 식별·영향도·발생가능성 평가.",
  COST_BENEFIT: "비용·편익 정량 분석. 재무 자료 필요.",
};

const ANALYZER_ORDER = AnalyzerSchema.options;

export function AnalyzerPicker() {
  const { control } = useFormContext<ProjectFormValues>();

  return (
    <Controller
      name="config.enabled_analyzers"
      control={control}
      render={({ field }) => {
        const selected = new Set(field.value);
        const toggle = (a: Analyzer) => {
          const next = new Set(selected);
          if (next.has(a)) next.delete(a);
          else next.add(a);
          field.onChange(ANALYZER_ORDER.filter((x) => next.has(x)));
        };
        const showCostBenefitWarn = selected.has("COST_BENEFIT");

        return (
          <TooltipProvider delayDuration={150}>
            <div className="flex flex-col gap-3">
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {ANALYZER_ORDER.map((a) => {
                  const checked = selected.has(a);
                  const id = `analyzer-${a}`;
                  return (
                    <label
                      key={a}
                      htmlFor={id}
                      className={cn(
                        "flex cursor-pointer items-center gap-2 rounded border p-3 transition-colors",
                        checked
                          ? "border-accent bg-bg-info"
                          : "border-border bg-bg hover:bg-bg-secondary",
                      )}
                    >
                      <Checkbox id={id} checked={checked} onCheckedChange={() => toggle(a)} />
                      <Label htmlFor={id} className="cursor-pointer text-sm text-fg">
                        {ANALYZER_LABEL[a]}
                      </Label>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <button
                            type="button"
                            aria-label={`${ANALYZER_LABEL[a]} 설명`}
                            className="ml-auto inline-flex h-5 w-5 items-center justify-center rounded-sm text-fg-tertiary hover:text-fg"
                            onClick={(e) => e.preventDefault()}
                          >
                            <Info className="h-3.5 w-3.5" />
                          </button>
                        </TooltipTrigger>
                        <TooltipContent className="max-w-xs text-xs">
                          {ANALYZER_DESC[a]}
                        </TooltipContent>
                      </Tooltip>
                    </label>
                  );
                })}
              </div>
              {showCostBenefitWarn ? (
                <p className="rounded border border-fg-warning/30 bg-bg-warning px-3 py-2 text-xs text-fg-warning">
                  Cost-Benefit 분석을 사용하려면 재무 자료가 필요합니다. 자료 검토 단계에서 재무
                  관련 출처를 채택해 주세요.
                </p>
              ) : null}
            </div>
          </TooltipProvider>
        );
      }}
    />
  );
}
