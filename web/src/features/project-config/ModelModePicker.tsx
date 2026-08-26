import { Controller, useFormContext } from "react-hook-form";
import { cn } from "@/lib/utils";
import type { ProjectFormValues } from "./schema";

// 품질 모드 - 프로젝트 단위 모델 선택(백엔드 stages._models_for가 소비).
// 역할별로 다른 모델을 쓴다(멀티 프로바이더):
//   economy  = 수집·검증 Haiku 4.5 + 본문 GPT-5.4-mini
//   standard = 수집 Haiku(기본) + 검증·본문 전역 설정 모델(기본 Sonnet 4.6)
//   premium  = 수집 Haiku + 검증 Sonnet 4.6 + 본문·파트 계획 Opus 5
//   (고급 수집도 Haiku - 2026-08-14 실측에서 Sonnet 수집의 품질 이득이 확인 안 됨)
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
  recommended?: boolean;
}[] = [
  {
    value: "premium",
    label: "고급",
    // 수집 담당까지 밝힌다 - 본문 모델만 쓰면 수집도 그 모델로 오해한다
    // (2026-08-14 실사용 오해). 수집은 어느 등급이든 Haiku다.
    model: "수집 Haiku 4.5 + 본문 Opus 5",
    cost: "비용 가장 높음",
    hint: "본문 품질이 가장 좋습니다. 근거를 더 많이 끌어 쓰고 단정이 덜 세서 사람이 고칠 일이 가장 적습니다",
    recommended: true,
  },
  {
    value: "standard",
    label: "표준",
    model: "수집 Haiku 4.5 + 본문 Sonnet 4.6",
    cost: "중간",
    hint: "쓸 만한 초안이 나오지만 수치와 단정은 사람이 한 번 훑어야 합니다",
  },
  {
    value: "economy",
    label: "절약",
    model: "수집 Haiku 4.5 + 본문 GPT-5.4-mini",
    cost: "비용 가장 낮음",
    hint: "구조와 흐름만 보는 시험용입니다. 사실 확인은 전부 사람 몫이라 납품용으로는 권하지 않습니다",
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
                  <span className="flex flex-wrap items-center gap-1.5">
                    <span className="text-sm font-semibold text-fg">{m.label}</span>
                    {/* 품질이 가장 좋은 쪽을 기본으로 두고 그렇다고 말한다 - 고르는
                        사람이 등급 이름만으로는 어느 쪽이 나은지 알 수 없다. */}
                    {m.recommended ? (
                      <span className="rounded-full border border-accent bg-bg-info px-2 py-0.5 text-[10px] font-medium text-fg-info">
                        추천
                      </span>
                    ) : null}
                  </span>
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
