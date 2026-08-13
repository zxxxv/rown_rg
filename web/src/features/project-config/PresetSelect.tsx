import { Controller, useFormContext } from "react-hook-form";
import { usePresets } from "@/api/presets";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { cn } from "@/lib/utils";
import type { ProjectFormValues } from "./schema";

export interface PresetSelectProps {
  onPresetChange: (preset: string | null) => void;
  disabled?: boolean;
}

interface PresetOption {
  value: string | null;
  label: string;
  description: string;
}

const FREE_TOPIC_OPTION: PresetOption = {
  value: null,
  label: "자유 주제 (프리셋 없음)",
  description: "정해진 목차 골격 없이 주제에 맞는 일반 목차를 새로 설계합니다.",
};

export function PresetSelect({ onPresetChange, disabled }: PresetSelectProps) {
  const { control } = useFormContext<ProjectFormValues>();
  const presetsQuery = usePresets();

  if (presetsQuery.isLoading) {
    return <LoadingSkeleton variant="card" count={3} />;
  }

  const rows = presetsQuery.data ?? [];
  const toOption = (p: (typeof rows)[number]): PresetOption => ({
    value: p.id,
    label: p.name,
    description: `${p.desc} · ${p.n_chapters}챕터 ${p.n_sections}섹션 골격`,
  });
  // 내가 저장한 목차 구성은 시스템 프리셋과 동급 선택지 - 같은 구성으로 여러
  // 정책을 분석하는 재사용 동선(2026-08-12 QA 2번 확장). 자유 주제는 빈 목차 시작.
  const groups: { title: string | null; options: PresetOption[] }[] = [
    { title: "시스템 프리셋", options: rows.filter((p) => p.scope !== "personal").map(toOption) },
    {
      title: "내 프리셋 - 저장한 목차 구성 불러오기",
      options: rows.filter((p) => p.scope === "personal").map(toOption),
    },
    { title: null, options: [FREE_TOPIC_OPTION] },
  ].filter((g) => g.options.length > 0);

  return (
    <Controller
      name="config.preset"
      control={control}
      render={({ field }) => (
        <fieldset disabled={disabled} className="flex flex-col gap-4 border-0 p-0">
          <legend className="sr-only">보고서 유형</legend>
          {groups.map((group) => (
            <div key={group.title ?? "free"} className="flex flex-col gap-2">
              {group.title ? (
                <p className="text-xs font-medium text-fg-secondary">{group.title}</p>
              ) : null}
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {group.options.map((opt) => {
                  const checked = field.value === opt.value;
                  const inputId = `preset-${opt.value ?? "free"}`;
                  return (
                    <label
                      key={opt.value ?? "free"}
                      htmlFor={inputId}
                      className={cn(
                        "relative flex cursor-pointer flex-col items-start gap-2 rounded-lg border p-4 text-left transition-colors",
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
                        value={opt.value ?? ""}
                        checked={checked}
                        disabled={disabled}
                        onChange={() => {
                          field.onChange(opt.value);
                          onPresetChange(opt.value);
                        }}
                        className="sr-only"
                      />
                      <div className="flex w-full items-center justify-between gap-2">
                        <span className="text-sm font-semibold text-fg">{opt.label}</span>
                        <span
                          aria-hidden
                          className={cn(
                            "h-3 w-3 rounded-full border",
                            checked ? "border-accent bg-accent" : "border-border-strong bg-bg",
                          )}
                        />
                      </div>
                      <p className="text-xs leading-relaxed text-fg-secondary">{opt.description}</p>
                    </label>
                  );
                })}
              </div>
            </div>
          ))}
        </fieldset>
      )}
    />
  );
}
