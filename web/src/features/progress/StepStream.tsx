import { useEffect, useRef } from "react";
import type { StreamChannel } from "@/api/ws-messages";
import { cn } from "@/lib/utils";

export interface StepStreamProps {
  channel: StreamChannel;
  text: string;
  className?: string;
}

const CHANNEL_CLASS: Record<StreamChannel, string> = {
  research_keywords: "font-mono",
  critic_thinking: "pl-4 border-l-2 border-accent",
  contradiction_explain: "",
};

export function StepStream({ channel, text, className }: StepStreamProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);

  const handleScroll = () => {
    const el = containerRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50;
    stickToBottomRef.current = atBottom;
  };

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !stickToBottomRef.current) return;
    if (text.length === 0) return;
    el.scrollTop = el.scrollHeight;
  }, [text]);

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className={cn(
        "max-h-64 overflow-y-auto whitespace-pre-wrap break-words rounded border border-border bg-bg-secondary p-3 text-sm leading-relaxed text-fg",
        CHANNEL_CLASS[channel],
        className,
      )}
      aria-live="polite"
    >
      {text || <span className="text-fg-tertiary">스트리밍 대기 중…</span>}
      {text ? (
        <span
          aria-hidden
          className="ml-0.5 inline-block h-3 w-1 animate-pulse bg-accent align-middle"
        />
      ) : null}
    </div>
  );
}
