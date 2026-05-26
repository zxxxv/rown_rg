import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type CheckpointNumber = 1 | 2 | 3 | 4 | 5 | 6;
export type DecisionIntent = "primary" | "secondary" | "danger";

export interface CheckpointDecision {
  label: string;
  intent: DecisionIntent;
  onClick: () => void;
  disabled?: boolean;
}

export interface ReviewCheckpointProps {
  number: CheckpointNumber;
  title: string;
  description: string;
  decisions: CheckpointDecision[];
  children?: ReactNode;
  className?: string;
}

const INTENT_VARIANT: Record<DecisionIntent, "default" | "secondary" | "destructive"> = {
  primary: "default",
  secondary: "secondary",
  danger: "destructive",
};

export function ReviewCheckpoint({
  number,
  title,
  description,
  decisions,
  children,
  className,
}: ReviewCheckpointProps) {
  return (
    <section
      className={cn("rounded-lg border border-border-info bg-bg-info p-6", className)}
      aria-label={`검토 지점 #${number}: ${title}`}
    >
      <header className="mb-4 flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span className="inline-flex h-7 min-w-[28px] items-center justify-center rounded-sm bg-accent px-2 font-mono text-xs font-medium text-accent-foreground">
            #{number}
          </span>
          <div>
            <h2 className="text-xl font-semibold text-fg">{title}</h2>
            <p className="mt-1 text-sm text-fg-secondary">{description}</p>
          </div>
        </div>
      </header>

      {children ? (
        <div className="mb-5 rounded border border-border bg-bg p-4">{children}</div>
      ) : null}

      <div className="flex flex-wrap items-center justify-end gap-2">
        {decisions.map((d) => (
          <Button
            key={`${d.intent}:${d.label}`}
            type="button"
            variant={INTENT_VARIANT[d.intent]}
            onClick={d.onClick}
            disabled={d.disabled}
          >
            {d.label}
          </Button>
        ))}
      </div>
    </section>
  );
}
