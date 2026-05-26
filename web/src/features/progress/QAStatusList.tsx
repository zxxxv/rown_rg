import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import { StatusDot, type StatusKind } from "@/components/data-display/StatusDot";
import { cn } from "@/lib/utils";
import { StepStream } from "./StepStream";

const QA_STEPS = ["Fact-check", "Consistency", "Style", "Critic"] as const;
type QAStep = (typeof QA_STEPS)[number];

export interface QAStatusListProps {
  currentStep: string | null;
  completedSteps: string[];
  failedSteps: string[];
  criticStream: string;
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
}: QAStatusListProps) {
  const [criticOpen, setCriticOpen] = useState(true);

  return (
    <ul className="flex flex-col gap-2">
      {QA_STEPS.map((step) => {
        const s = statusOf(step, currentStep, completedSteps, failedSteps);
        const isCritic = step === "Critic";
        const isActiveCritic = isCritic && s.kind === "info";
        return (
          <li
            key={step}
            className={cn(
              "rounded border p-3",
              s.kind === "info" ? "border-accent bg-bg-info" : "border-border bg-bg",
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-sm font-medium text-fg">{step}</span>
              <StatusDot kind={s.kind} label={s.label} />
            </div>
            {isCritic && (criticStream || isActiveCritic) ? (
              <div className="mt-2">
                <button
                  type="button"
                  className="inline-flex items-center gap-1 text-xs text-fg-secondary hover:text-fg"
                  onClick={() => setCriticOpen((v) => !v)}
                >
                  {criticOpen ? (
                    <ChevronDown className="h-3 w-3" />
                  ) : (
                    <ChevronRight className="h-3 w-3" />
                  )}
                  사고 과정
                </button>
                {criticOpen ? (
                  <div className="mt-2">
                    <StepStream channel="critic_thinking" text={criticStream} />
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
