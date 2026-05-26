import { Controller, useFormContext } from "react-hook-form";
import { type DepthMode, DepthModeSchema } from "@/api/types";
import { cn } from "@/lib/utils";
import type { ProjectFormValues } from "./schema";

interface DepthInfo {
  label: string;
  pages: string;
  cost: string;
  hours: string;
  hint?: string;
}

const DEPTH_INFO: Record<DepthMode, DepthInfo> = {
  outline_only: {
    label: "Outline Only",
    pages: "10~30p",
    cost: "1~3만원",
    hours: "~1시간",
    hint: "목차·요약 수준의 가벼운 초안.",
  },
  standard: {
    label: "Standard",
    pages: "40~80p",
    cost: "3~7만원",
    hours: "1~2시간",
    hint: "주요 섹션을 본문 형식으로 작성.",
  },
  full_report: {
    label: "Full Report",
    pages: "200~300p",
    cost: "10~22만원",
    hours: "5~10시간",
    hint: "회사 표준 보고서 분량. 기본값.",
  },
  deep_dive: {
    label: "Deep Dive",
    pages: "300~500p",
    cost: "20~40만원",
    hours: "10~20시간",
    hint: "민감 주제에 대한 심층 분석. 비용 큼.",
  },
};

const DEPTH_ORDER = DepthModeSchema.options;

export interface DepthSelectorProps {
  disabled?: boolean;
}

export function DepthSelector({ disabled }: DepthSelectorProps) {
  const { control } = useFormContext<ProjectFormValues>();

  return (
    <Controller
      name="config.depth_mode"
      control={control}
      render={({ field }) => (
        <fieldset disabled={disabled} className="flex flex-col gap-2 border-0 p-0">
          <legend className="sr-only">작성 깊이</legend>
          {DEPTH_ORDER.map((d) => {
            const info = DEPTH_INFO[d];
            const checked = field.value === d;
            const inputId = `depth-${d}`;
            const isDefault = d === "full_report";
            return (
              <label
                key={d}
                htmlFor={inputId}
                className={cn(
                  "relative flex cursor-pointer items-start gap-3 rounded-lg border p-4 transition-colors",
                  "focus-within:ring-2 focus-within:ring-accent focus-within:ring-offset-2",
                  checked
                    ? "border-accent bg-bg-info"
                    : "border-border bg-bg hover:border-border-strong hover:bg-bg-secondary",
                  disabled && "cursor-not-allowed opacity-60",
                )}
              >
                <input
                  id={inputId}
                  type="radio"
                  name={field.name}
                  value={d}
                  checked={checked}
                  disabled={disabled}
                  onChange={() => field.onChange(d)}
                  className="sr-only"
                />
                <span
                  aria-hidden
                  className={cn(
                    "mt-1 h-3 w-3 shrink-0 rounded-full border",
                    checked ? "border-accent bg-accent" : "border-border-strong bg-bg",
                  )}
                />
                <div className="flex flex-1 flex-col gap-1">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-fg">{info.label}</span>
                    {isDefault ? (
                      <span className="rounded-sm border border-border bg-bg-secondary px-1.5 py-0.5 text-[10px] text-fg-tertiary">
                        기본값
                      </span>
                    ) : null}
                  </div>
                  <div className="flex flex-wrap gap-x-3 font-mono text-xs text-fg-secondary">
                    <span>{info.pages}</span>
                    <span>{info.cost}</span>
                    <span>{info.hours}</span>
                  </div>
                  {info.hint ? <p className="text-xs text-fg-tertiary">{info.hint}</p> : null}
                </div>
              </label>
            );
          })}
        </fieldset>
      )}
    />
  );
}
