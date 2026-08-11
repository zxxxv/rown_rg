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
const MODES: {
  value: "standard" | "economy" | "premium";
  label: string;
  model: string;
  cost: string;
  quality: string;
  hint: string;
}[] = [
  {
    value: "standard",
    label: "표준 (품질 우선)",
    model: "Sonnet 4.6",
    cost: "14절 보고서 한 편 약 $3~5",
    quality: "그대로 납품하지 않고 사람이 한 번 훑는 초안",
    hint: "인용한 근거가 그 문장을 실제로 뒷받침하는 비율이 실측 약 50%였습니다(1절·표본 20). 실패는 주로 근거에 없는 수치·날짜를 지어내는 형태라, 표와 수치는 검토 화면에서 확인하세요.",
  },
  {
    value: "premium",
    label: "고급 (품질 최우선)",
    model: "수집 Sonnet 4.6 + 본문 Opus 5",
    cost: "한 편 약 $5~7 (본문 단가가 표준의 1.6배)",
    quality: "검토 부담이 가장 적은 납품 초안",
    hint: "같은 실측에서 근거 뒷받침 비율 80%. 남은 실패도 수치 창작이 아니라 근거보다 세게 단정하는 쪽이라 고치기 쉽습니다. 같은 자료에서 더 많이 끌어씁니다(근거 활용률 0.91 대 0.75). 수집은 도구를 도는 루프라 모델을 올려도 회수량이 늘지 않아 표준과 같습니다.",
  },
  {
    value: "economy",
    label: "절약 (저비용)",
    model: "Haiku 4.5 + GPT-5.4-mini",
    cost: "한 편 약 $2~3 (본문 단가가 표준의 0.4배)",
    quality: "구조와 흐름만 보는 테스트·초안",
    hint: "근거가 빈약한 절에서 파트 구성이 무너져 내용이 얕아집니다(그래서 파트 계획만 Sonnet 4.6으로 돌립니다). 문장은 읽을 만하지만 사실 확인을 전부 사람이 한다는 전제에서만 쓰세요.",
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
                  <span className="text-xs text-fg-secondary">{m.quality}</span>
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
