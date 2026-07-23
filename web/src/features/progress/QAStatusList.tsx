import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import type { StreamChannel } from "@/api/ws-messages";
import { StatusDot, type StatusKind } from "@/components/data-display/StatusDot";
import { cn } from "@/lib/utils";
import { StepStream } from "./StepStream";

const QA_STEPS = ["Fact-check", "Consistency", "Style", "Critic"] as const;
type QAStep = (typeof QA_STEPS)[number];

// 완료 시 표시되는 데모 검증 결과 요약.
const QA_RESULT: Record<QAStep, string> = {
  "Fact-check": "주장 14건 중 13건 출처 확인 · 1건 보완 권고",
  Consistency: "2.3절↔4.2절 고령화율 충돌 1건 — 자료 A 기준으로 통일",
  Style: "문체·표기 일관성 통과 · 표 3건 회사 양식 적용",
  Critic: "외부 검토 관점 약점 1건 제기 → 재검증 반영",
};

// 스트림이 달린 검사 (펼침형 상세).
const STREAMED: Partial<Record<QAStep, { channel: StreamChannel; label: string }>> = {
  Consistency: { channel: "contradiction_explain", label: "발견 내역" },
  Critic: { channel: "critic_thinking", label: "사고 과정" },
};

export interface QAStatusListProps {
  currentStep: string | null;
  completedSteps: string[];
  failedSteps: string[];
  criticStream: string;
  consistencyStream: string;
}

function statusOf(
  step: QAStep,
  currentStep: string | null,
  completed: string[],
  failed: string[],
): { kind: StatusKind; label: string } {
  if (failed.includes(step)) return { kind: "danger", label: "실패" };
  if (completed.includes(step)) return { kind: "success", label: "통과" };
  if (currentStep === step) return { kind: "info", label: "진행 중" };
  return { kind: "tertiary", label: "대기" };
}

export function QAStatusList({
  currentStep,
  completedSteps,
  failedSteps,
  criticStream,
  consistencyStream,
}: QAStatusListProps) {
  const [open, setOpen] = useState<Record<string, boolean>>({ Consistency: true, Critic: true });

  return (
    <ul className="flex flex-col gap-2">
      {QA_STEPS.map((step) => {
        const s = statusOf(step, currentStep, completedSteps, failedSteps);
        const streamCfg = STREAMED[step];
        const streamText = step === "Critic" ? criticStream : consistencyStream;
        const isActive = s.kind === "info";
        const isDone = s.kind === "success";
        const isOpen = open[step] ?? true;
        return (
          <li
            key={step}
            className={cn(
              "rounded border p-3",
              s.kind === "info"
                ? "border-accent bg-bg-info"
                : s.kind === "danger"
                  ? "border-fg-danger/40 bg-bg-danger"
                  : "border-border bg-bg",
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-medium text-fg">{step}</span>
              <StatusDot kind={s.kind} label={s.label} />
            </div>

            {isDone ? <p className="mt-1.5 text-xs text-fg-secondary">{QA_RESULT[step]}</p> : null}

            {streamCfg && (streamText || isActive) ? (
              <div className="mt-2">
                <button
                  type="button"
                  className="inline-flex items-center gap-1 text-xs text-fg-secondary hover:text-fg"
                  onClick={() => setOpen((prev) => ({ ...prev, [step]: !isOpen }))}
                >
                  {isOpen ? (
                    <ChevronDown className="h-3 w-3" />
                  ) : (
                    <ChevronRight className="h-3 w-3" />
                  )}
                  {streamCfg.label}
                </button>
                {isOpen ? (
                  <div className="mt-2">
                    <StepStream channel={streamCfg.channel} text={streamText} />
                  </div>
                ) : null}
              </div>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
