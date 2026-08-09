import { cn } from "@/lib/utils";

export interface ConfidenceBadgeProps {
  value: number;
  className?: string;
}

function bucket(value: number): "danger" | "warning" | "success" {
  if (value < 0.5) return "danger";
  if (value < 0.8) return "warning";
  return "success";
}

const BUCKET_CLASS = {
  danger: "bg-bg-danger text-fg-danger border-fg-danger/30",
  warning: "bg-bg-warning text-fg-warning border-fg-warning/30",
  success: "bg-bg-success text-fg-success border-fg-success/30",
} as const;

export function ConfidenceBadge({ value, className }: ConfidenceBadgeProps) {
  const clamped = Math.max(0, Math.min(1, value));
  const variant = bucket(clamped);
  return (
    <span
      role="status"
      aria-label={`신뢰도 ${clamped.toFixed(2)}`}
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 font-mono text-xs",
        BUCKET_CLASS[variant],
        className,
      )}
    >
      <span className="text-fg-tertiary">신뢰도</span>
      <span className="font-medium">{clamped.toFixed(2)}</span>
    </span>
  );
}
