import type { LucideIcon } from "lucide-react";
import type { ComponentType, ReactNode } from "react";
import { cn } from "@/lib/utils";

export interface EmptyStateProps {
  icon?: LucideIcon | ComponentType<{ className?: string }>;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({ icon: Icon, title, description, action, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded border border-dashed border-border bg-bg-secondary p-10 text-center",
        className,
      )}
    >
      {Icon ? <Icon className="h-10 w-10 text-fg-tertiary" aria-hidden /> : null}
      <h3 className="text-base font-semibold text-fg">{title}</h3>
      {description ? <p className="text-sm text-fg-secondary">{description}</p> : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  );
}
