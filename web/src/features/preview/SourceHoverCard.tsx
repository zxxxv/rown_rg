import {
  autoUpdate,
  FloatingPortal,
  flip,
  offset,
  shift,
  useDismiss,
  useFloating,
  useFocus,
  useHover,
  useInteractions,
  useRole,
} from "@floating-ui/react";
import { ExternalLink } from "lucide-react";
import { useState } from "react";
import type { SectionCitation } from "@/api/types";

const RELIABILITY_LABEL: Record<string, string> = {
  high: "신뢰도 높음",
  medium: "신뢰도 중간",
  low: "신뢰도 낮음",
};

export interface CitationHoverCardProps {
  /** 본문에 표시되는 인용 번호([N]) */
  number: number;
  /** 이 번호가 가리키는 출처 - 섹션 응답의 citations에서 옴(추가 fetch 없음) */
  citation: SectionCitation;
}

/** 본문 [N] 마커 자리에 렌더되는 인용 배지 + 호버 출처 카드. */
export function CitationHoverCard({ number, citation }: CitationHoverCardProps) {
  const [open, setOpen] = useState(false);
  const { refs, floatingStyles, context } = useFloating({
    open,
    onOpenChange: setOpen,
    placement: "top",
    middleware: [offset(8), flip(), shift({ padding: 8 })],
    whileElementsMounted: autoUpdate,
  });
  const hover = useHover(context, { delay: { open: 200, close: 100 }, move: false });
  const focus = useFocus(context);
  const dismiss = useDismiss(context);
  const role = useRole(context, { role: "tooltip" });
  const { getReferenceProps, getFloatingProps } = useInteractions([hover, focus, dismiss, role]);

  const reliabilityLabel = citation.reliability
    ? (RELIABILITY_LABEL[citation.reliability] ?? null)
    : null;

  return (
    <>
      <button
        type="button"
        ref={refs.setReference}
        {...getReferenceProps()}
        className="mx-0.5 inline-flex h-4 items-center justify-center rounded-sm bg-bg-info px-1 align-middle font-mono text-[10px] font-medium text-fg-info hover:bg-bg-info/80 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        aria-label={`출처 ${number}: ${citation.title}`}
      >
        [{number}]
      </button>
      {open ? (
        <FloatingPortal>
          <div
            ref={refs.setFloating}
            style={floatingStyles}
            {...getFloatingProps()}
            className="z-50 w-80 max-w-[90vw] rounded-lg border border-border bg-bg p-4 text-left text-sm shadow-lg"
          >
            <div className="flex flex-col gap-2">
              <header className="flex items-start justify-between gap-2">
                <h4 className="text-sm font-semibold text-fg">{citation.title}</h4>
                {reliabilityLabel ? (
                  <span className="shrink-0 rounded-sm border border-border px-1.5 py-0.5 text-[10px] text-fg-secondary">
                    {reliabilityLabel}
                  </span>
                ) : null}
              </header>
              {citation.url ? (
                <a
                  href={citation.url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 self-end text-xs text-fg-info hover:underline"
                >
                  원본 보기 <ExternalLink className="h-3 w-3" />
                </a>
              ) : null}
            </div>
          </div>
        </FloatingPortal>
      ) : null}
    </>
  );
}
