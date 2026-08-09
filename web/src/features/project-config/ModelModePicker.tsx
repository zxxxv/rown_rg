import { Controller, useFormContext } from "react-hook-form";
import { cn } from "@/lib/utils";
import type { ProjectFormValues } from "./schema";

// 품질 모드 - 프로젝트 단위 모델 선택(백엔드 stages._models_for가 소비).
// 역할별로 다른 모델을 쓴다(멀티 프로바이더):
//   economy  = 수집·검증 Haiku 4.5 + 본문 GPT-5.4-mini
//   standard = 수집·검증·본문 전역 설정 모델(기본 Sonnet 4.6)
//   파트 계획은 두 모드 모두 Sonnet 4.6(_PLAN_MODEL) - 절당 1콜이라 비용이 무시할 수준.
// 라벨을 'Haiku'로만 적어두면 사실과 다르다(2026-08-10 실측: 절약 런의 본문은 전부
// gpt-5.4-mini였다).
const MODES: {
  value: "standard" | "economy";
  label: string;
  model: string;
  hint: string;
}[] = [
  {
    value: "standard",
    label: "표준 (품질 우선)",
    model: "Sonnet 4.6",
    hint: "수집·본문·검증 모두 Sonnet 4.6. 납품용 - 페르소나·분량 목표를 온전히 실현합니다.",
  },
  {
    value: "economy",
    label: "절약 (저비용)",
    model: "Haiku 4.5 + GPT-5.4-mini",
    hint: "수집·검증은 Haiku, 본문은 GPT-5.4-mini. 테스트·초안용 - 비용이 1/5 수준입니다.",
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
