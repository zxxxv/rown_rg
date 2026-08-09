import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export type StatTone = "default" | "warning" | "danger" | "success";

const TONE_BAR_CLASS: Record<StatTone, string> = {
  default: "bg-accent",
  warning: "bg-fg-warning",
  danger: "bg-fg-danger",
  success: "bg-fg-success",
};

const TONE_TEXT_CLASS: Record<StatTone, string> = {
  default: "text-fg",
  warning: "text-fg-warning",
  danger: "text-fg-danger",
  success: "text-fg-success",
};

export interface StatCardProps {
  label: string;
  value: string;
  hint?: string;
  icon?: LucideIcon;
  tone?: StatTone;
  progress?: { current: number; max: number };
  className?: string;
}

export function StatCard({
  label,
  value,
  hint,
  icon: Icon,
  tone = "default",
  progress,
  className,
}: StatCardProps) {
  const pct = progress
    ? Math.max(0, Math.min(100, (progress.current / Math.max(1, progress.max)) * 100))
    : null;

  return (
    <div
      className={cn(
        "flex flex-col gap-2 rounded-lg border bg-bg p-4",
        tone === "warning" && "border-fg-warning/30 bg-bg-warning/40",
        tone === "danger" && "border-fg-danger/30 bg-bg-danger/40",
        tone === "success" && "border-fg-success/30 bg-bg-success/40",
        tone === "default" && "border-border",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-fg-tertiary">{label}</span>
        {Icon ? <Icon className="h-4 w-4 text-fg-tertiary" aria-hidden /> : null}
      </div>
      <span className={cn("font-mono text-2xl font-semibold", TONE_TEXT_CLASS[tone])}>{value}</span>
      {pct !== null ? (
        <div className="flex flex-col gap-1">
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-bg-tertiary">
            <div
              className={cn("h-full transition-[width]", TONE_BAR_CLASS[tone])}
              style={{ width: `${pct}%` }}
            />
          </div>
          {hint ? <span className="text-xs text-fg-tertiary">{hint}</span> : null}
        </div>
      ) : hint ? (
        <span className="text-xs text-fg-tertiary">{hint}</span>
      ) : null}
    </div>
  );
}
