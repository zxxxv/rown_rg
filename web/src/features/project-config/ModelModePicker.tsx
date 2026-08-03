import { Controller, useFormContext } from "react-hook-form";
import { cn } from "@/lib/utils";
import type { ProjectFormValues } from "./schema";

// 품질 모드 — 프로젝트 단위 모델 선택(백엔드 stages._models_for가 소비).
// economy=Haiku(플래너·수집·작성·검증 전 역할), standard=전역 설정 모델(기본 Sonnet).
const MODES: {
  value: "standard" | "economy";
  label: string;
  model: string;
  hint: string;
}[] = [
  {
    value: "standard",
    label: "표준 (품질 우선)",
    model: "Claude Sonnet",
    hint: "납품용 보고서 — 페르소나·분량 목표를 온전히 실현합니다.",
  },
  {
    value: "economy",
    label: "절약 (저비용)",
    model: "Claude Haiku",
    hint: "테스트·초안용 — 비용이 수 분의 1, 품질은 다소 낮습니다.",
  },
];

export interface ModelModePickerProps {
  disabled?: boolean;
}

export function ModelModePicker({ disabled }: ModelModePickerProps) {
  const { control } = useFormContext<ProjectFormValues>();

  return (
    <Controller
      name="config.model_mode"
      control={control}
      render={({ field }) => (
        <fieldset
          disabled={disabled}
          className="grid grid-cols-1 gap-2 border-0 p-0 sm:grid-cols-2"
        >
          <legend className="sr-only">모델 품질</legend>
          {MODES.map((m) => {
            const checked = field.value === m.value;
            const inputId = `model-mode-${m.value}`;
            return (
              <label
                key={m.value}
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
                  value={m.value}
                  checked={checked}
                  disabled={disabled}
                  onChange={() => field.onChange(m.value)}
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
                  <span className="text-sm font-semibold text-fg">{m.label}</span>
                  <span className="font-mono text-xs text-fg-secondary">{m.model}</span>
                  <p className="text-xs text-fg-tertiary">{m.hint}</p>
                </div>
              </label>
            );
          })}
        </fieldset>
      )}
    />
  );
}
