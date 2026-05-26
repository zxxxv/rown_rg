import { Check, X } from "lucide-react";
import { type PhaseName, PhaseNameSchema } from "@/api/ws-messages";
import { cn } from "@/lib/utils";

const PHASE_LABEL: Record<PhaseName, string> = {
  research: "자료조사",
  indexing: "인덱싱",
  writing: "작성",
  qa: "검증",
  export: "출력",
};

const PHASE_ORDER = PhaseNameSchema.options;

export interface PhaseTrackerProps {
  activePhase: PhaseName;
  phaseStatus: "started" | "completed";
  completedPhases: Set<PhaseName>;
  failedPhases?: Set<PhaseName>;
}

type CardKind = "pending" | "running" | "completed" | "failed";

function kindOf(
  phase: PhaseName,
  active: PhaseName,
  phaseStatus: "started" | "completed",
  completed: Set<PhaseName>,
  failed?: Set<PhaseName>,
): CardKind {
  if (failed?.has(phase)) return "failed";
  if (completed.has(phase)) return "completed";
  if (phase === active) {
    return phaseStatus === "completed" ? "completed" : "running";
  }
  const activeIdx = PHASE_ORDER.indexOf(active);
  const phaseIdx = PHASE_ORDER.indexOf(phase);
  if (phaseIdx < activeIdx) return "completed";
  return "pending";
}

const KIND_CLASS: Record<CardKind, string> = {
  pending: "border-border bg-bg",
  running: "border-accent bg-bg-info",
  completed: "border-fg-success/40 bg-bg-success",
  failed: "border-fg-danger/40 bg-bg-danger",
};

export function PhaseTracker({
  activePhase,
  phaseStatus,
  completedPhases,
  failedPhases,
}: PhaseTrackerProps) {
  return (
    <ol className="grid grid-cols-2 gap-2 md:grid-cols-5">
      {PHASE_ORDER.map((phase) => {
        const kind = kindOf(phase, activePhase, phaseStatus, completedPhases, failedPhases);
        return (
          <li
            key={phase}
            className={cn(
              "flex flex-col gap-1.5 rounded-lg border p-3 transition-colors",
              KIND_CLASS[kind],
            )}
            aria-label={`${PHASE_LABEL[phase]} — ${kind}`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-medium text-fg">{PHASE_LABEL[phase]}</span>
              {kind === "completed" ? (
                <Check className="h-3.5 w-3.5 text-fg-success" aria-hidden />
              ) : kind === "failed" ? (
                <X className="h-3.5 w-3.5 text-fg-danger" aria-hidden />
              ) : kind === "running" ? (
                <span aria-hidden className="h-2 w-2 animate-pulse rounded-full bg-accent" />
              ) : (
                <span aria-hidden className="h-2 w-2 rounded-full bg-fg-tertiary/40" />
              )}
            </div>
            <div className="h-1 w-full overflow-hidden rounded-full bg-bg-tertiary">
              <div
                className={cn(
                  "h-full transition-[width]",
                  kind === "completed"
                    ? "w-full bg-fg-success"
                    : kind === "running"
                      ? "w-1/2 bg-accent"
                      : kind === "failed"
                        ? "w-full bg-fg-danger"
                        : "w-0 bg-fg-tertiary",
                )}
              />
            </div>
          </li>
        );
      })}
    </ol>
  );
}
