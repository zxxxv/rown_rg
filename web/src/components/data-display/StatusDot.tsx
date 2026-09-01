import { cn } from "@/lib/utils";

export type StatusKind = "success" | "info" | "warning" | "danger" | "tertiary";

const KIND_CLASS: Record<StatusKind, string> = {
  success: "bg-fg-success",
  info: "bg-fg-info",
  warning: "bg-fg-warning",
  danger: "bg-fg-danger",
  tertiary: "bg-fg-tertiary",
};

export interface StatusDotProps {
  kind: StatusKind;
  label?: string;
  className?: string;
}

export function StatusDot({ kind, label, className }: StatusDotProps) {
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      <span aria-hidden className={cn("h-2 w-2 rounded-full", KIND_CLASS[kind])} />
      {label ? <span className="whitespace-nowrap text-xs text-fg-secondary">{label}</span> : null}
    </span>
  );
}
