import { useFormContext, useWatch } from "react-hook-form";
import { Link } from "react-router-dom";
import { useListPersonalPrompts } from "@/api/prompts";
import { cn } from "@/lib/utils";
import type { ProjectFormValues } from "./schema";

// ─── 작성 규칙 선택 - 이 보고서 전체에 적용할 문체·출처·시각자료 규칙 ───
// 규칙은 절이 아니라 보고서 단위 계약이라 여기서 한 번 고르고 프로젝트에 고정된다
// (config.rules). 고르지 않으면 회사 표준 3종이 그대로 적용된다.

/** 시스템 규칙 3종의 자리. 개인 규칙이 같은 자리를 지정하면 그 자리를 대체한다. */
const SLOT_LABELS: Record<string, string> = {
  agent_source_rules: "출처 규칙",
  agent_visual_rules: "시각자료 규칙",
  agent_writing_style: "문체 규칙",
};

export function RulePicker({ disabled }: { disabled?: boolean }) {
  const { setValue } = useFormContext<ProjectFormValues>();
  const selected = useWatch<ProjectFormValues, "config.rules">({ name: "config.rules" }) ?? [];
  const query = useListPersonalPrompts("rule");
  const rules = query.data ?? [];

  const toggle = (id: string) => {
    const next = selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id];
    setValue("config.rules", next, { shouldDirty: true });
  };

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-fg-secondary">
        회사 표준 규칙 3종(출처 · 시각자료 · 문체)이 기본으로 적용됩니다. 내가 만든 규칙을 고르면
        같은 자리를 대체하거나 뒤에 추가됩니다.
      </p>
      {query.isLoading ? (
        <p className="text-xs text-fg-tertiary">불러오는 중…</p>
      ) : rules.length === 0 ? (
        <p className="text-xs text-fg-tertiary">
          만들어 둔 작성 규칙이 없습니다.{" "}
          <Link to="/prompts" className="underline">
            프롬프트 화면
          </Link>
          에서 만들면 여기에 나타납니다.
        </p>
      ) : (
        <div className="flex flex-col gap-1.5">
          {rules.map((rule) => {
            const active = selected.includes(rule.id);
            const slot = rule.base_ref ? SLOT_LABELS[rule.base_ref] : undefined;
            return (
              <button
                key={rule.id}
                type="button"
                disabled={disabled}
                onClick={() => toggle(rule.id)}
                aria-pressed={active}
                className={cn(
                  "flex items-start gap-2 rounded border px-3 py-2 text-left transition-colors",
                  active
                    ? "border-accent bg-bg-info"
                    : "border-border bg-bg hover:border-fg-tertiary",
                  disabled && "opacity-60",
                )}
              >
                <span className="mt-0.5 shrink-0 font-mono text-xs text-fg-tertiary">
                  {active ? "[v]" : "[ ]"}
                </span>
                <span className="flex flex-col gap-0.5">
                  <span className="text-sm text-fg">{rule.name}</span>
                  <span className="text-xs text-fg-tertiary">
                    {slot ? `${slot} 자리를 대체합니다` : "기존 규칙 뒤에 추가됩니다"}
                    {rule.description ? ` · ${rule.description}` : ""}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
