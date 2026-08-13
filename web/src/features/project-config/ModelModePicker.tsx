import { Controller, useFormContext } from "react-hook-form";
import { cn } from "@/lib/utils";
import type { ProjectFormValues } from "./schema";

// 품질 모드 - 프로젝트 단위 모델 선택(백엔드 stages._models_for가 소비).
// 역할별로 다른 모델을 쓴다(멀티 프로바이더):
//   economy  = 수집·검증 Haiku 4.5 + 본문 GPT-5.4-mini
//   standard = 수집·검증·본문 전역 설정 모델(기본 Sonnet 4.6)
//   premium  = 수집·검증 Sonnet 4.6 + 본문·파트 계획 Opus 5(검색은 sonnet, 작성만 opus)
//   파트 계획은 두 모드 모두 Sonnet 4.6(_PLAN_MODEL) - 절당 1콜이라 비용이 무시할 수준.
// 라벨을 'Haiku'로만 적어두면 사실과 다르다(2026-08-10 실측: 절약 런의 본문은 전부
// gpt-5.4-mini였다).
// 숫자를 적지 않는다 - 금액은 절 수·자료량·분량 목표에 따라 몇 배씩 갈리고, 배수도
// 표본 하나에서 나온 값이라 조건이 바뀌면 바로 틀린 말이 된다(2026-08-12 지적).
// 사람이 고를 때 필요한 건 정확한 수치가 아니라 '어느 쪽이 비싸고 어느 쪽이 손이 덜
// 가는가'다. 실제 비용은 화면 상단의 사용량 표시로 확인한다.
const MODES: {
  value: "standard" | "economy" | "premium";
  label: string;
  model: string;
  cost: string;
  hint: string;
}[] = [
  {
    value: "standard",
    label: "표준",
    // 수집 담당까지 밝힌다 - 'Sonnet 4.6'만 쓰면 수집도 Sonnet으로 오해한다
    // (2026-08-14 실사용 오해 - 실제 수집 기본은 Haiku, 작성이 Sonnet).
    model: "수집 Haiku 4.5 + 본문 Sonnet 4.6",
    cost: "기준",
    hint: "사람이 한 번 훑는 초안 - 수치와 표는 검토 화면에서 확인하세요",
  },
  {
    value: "premium",
    label: "고급",
    model: "수집 Sonnet 4.6 + 본문 Opus 5",
    cost: "비용 더 높음",
    hint: "검토 부담이 가장 적음 - 근거를 더 많이 끌어쓰고 단정이 덜 세다",
  },
  {
    value: "economy",
    label: "절약",
    model: "Haiku 4.5 + GPT-5.4-mini",
    cost: "비용 낮음",
    hint: "구조와 흐름만 보는 테스트용 - 사실 확인을 전부 사람이 한다는 전제",
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
                  <span className="text-xs font-medium text-fg-secondary">{m.cost}</span>
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
