import { cn } from "@/lib/utils";

export interface KbdHintProps {
  keys: string[];
  className?: string;
}

export function KbdHint({ keys, className }: KbdHintProps) {
  return (
    <span className={cn("inline-flex items-center gap-0.5 font-mono text-xs", className)}>
      {keys.map((k) => (
        <kbd
          key={k}
          className="inline-flex h-5 min-w-[20px] items-center justify-center rounded-sm border border-border bg-bg-secondary px-1 text-fg-secondary"
        >
          {k}
        </kbd>
      ))}
    </span>
  );
}
