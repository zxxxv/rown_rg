import { Pencil } from "lucide-react";
import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";

export interface WritingDraftProps {
  title: string | null;
  text: string;
  className?: string;
}

/**
 * 작성 단계에서 본문이 타이핑되며 써지는 모습을 보여주는 라이브 패널.
 * 스트리밍 중 부분 마크다운이 깨지지 않도록 평문(whitespace-pre-wrap)으로 렌더한다.
 * 자동 스크롤 로직은 StepStream과 동일.
 */
export function WritingDraft({ title, text, className }: WritingDraftProps) {
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
    <div className={cn("flex flex-col overflow-hidden rounded-lg border border-border", className)}>
      <div className="flex items-center gap-2 border-b border-border bg-bg-secondary px-3 py-2">
        <Pencil className="h-3.5 w-3.5 text-accent" aria-hidden />
        <span className="text-xs font-medium text-fg-secondary">
          {title ? (
            <>
              작성 중 · <span className="text-fg">{title}</span>
            </>
          ) : (
            "작성 대기 중…"
          )}
        </span>
      </div>
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="max-h-80 min-h-40 overflow-y-auto whitespace-pre-wrap break-words bg-bg p-4 text-sm leading-relaxed text-fg"
        aria-live="polite"
      >
        {text || (
          <span className="text-fg-tertiary">초안이 생성되면 여기에 본문이 표시됩니다.</span>
        )}
        {text ? (
          <span
            aria-hidden
            className="ml-0.5 inline-block h-3.5 w-1 animate-pulse bg-accent align-middle"
          />
        ) : null}
      </div>
    </div>
  );
}
