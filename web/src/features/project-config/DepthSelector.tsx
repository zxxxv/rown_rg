import { Controller, useFormContext } from "react-hook-form";
import { type DepthMode, DepthModeSchema } from "@/api/types";
import { cn } from "@/lib/utils";
import type { ProjectFormValues } from "./schema";

interface DepthInfo {
  label: string;
  /** 배경 요약(RAPTOR) 트리 층수 — 깊이가 실제로 조절하는 값 */
  raptor: string;
  hint?: string;
}

// 예전의 "200~300p" 식 페이지 표기는 제거(2026-08-05) — 분량은 깊이가 아니라
// 목차 절 수·에이전트 분량 목표가 결정한다. 깊이의 실효 = 배경 요약 트리 층수.
const DEPTH_INFO: Record<DepthMode, DepthInfo> = {
  outline_only: {
    label: "Outline Only",
    raptor: "배경 요약 없음",
    hint: "자료 원문 검색만 사용 — 목차·요약 수준의 가벼운 초안용.",
  },
  standard: {
    label: "Standard",
    raptor: "배경 요약 1층",
    hint: "자료를 의미별로 묶은 요약 1층을 만들어 작성 시 배경 맥락으로 활용.",
  },
  full_report: {
    label: "Full Report",
    raptor: "배경 요약 2층",
    hint: "요약을 다시 묶은 상위 요약(요약의 요약)까지 쌓아 문서 전체 맥락이 깊어짐 — 회사 표준.",
  },
  deep_dive: {
    label: "Deep Dive",
    raptor: "배경 요약 3층",
    hint: "최대 추상화 — 자료가 많고 민감·복잡한 주제의 심층 분석용.",
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
                  <div className="font-mono text-xs text-fg-secondary">
                    <span>{info.raptor}</span>
                  </div>
                  {info.hint ? <p className="text-xs text-fg-tertiary">{info.hint}</p> : null}
                </div>
              </label>
            );
          })}
          <p className="text-xs text-fg-tertiary">
            깊이는 자료를 계층 요약(RAPTOR)해 작성에 곁들이는 <strong>배경 맥락의 층수</strong>를
            조절합니다. 보고서 분량은 깊이가 아니라 목차의 절 수와 절별 분석 에이전트의 분량 설정에
            따라 결정됩니다.
          </p>
        </fieldset>
      )}
    />
  );
}
