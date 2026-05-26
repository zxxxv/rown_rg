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
import { useSourceRef } from "@/api/sections";
import { ConfidenceBadge } from "@/components/data-display/ConfidenceBadge";

export interface SourceHoverCardProps {
  srcId: string;
}

export function SourceHoverCard({ srcId }: SourceHoverCardProps) {
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

  const sourceQuery = useSourceRef(open ? srcId : null);
  const source = sourceQuery.data;

  return (
    <>
      <button
        type="button"
        ref={refs.setReference}
        {...getReferenceProps()}
        className="mx-0.5 inline-flex h-4 items-center justify-center rounded-sm bg-bg-info px-1 align-middle font-mono text-[10px] font-medium text-fg-info hover:bg-bg-info/80 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        aria-label={`출처 ${srcId}`}
      >
        {srcId}
      </button>
      {open ? (
        <FloatingPortal>
          <div
            ref={refs.setFloating}
            style={floatingStyles}
            {...getFloatingProps()}
            className="z-50 w-80 max-w-[90vw] rounded-lg border border-border bg-bg p-4 text-left text-sm shadow-lg"
          >
            {sourceQuery.isLoading || !source ? (
              <p className="text-xs text-fg-tertiary">불러오는 중…</p>
            ) : (
              <div className="flex flex-col gap-2">
                <header className="flex items-start justify-between gap-2">
                  <h4 className="text-sm font-semibold text-fg">{source.title}</h4>
                  <ConfidenceBadge value={source.reliability} />
                </header>
                <dl className="flex flex-wrap gap-x-3 font-mono text-xs text-fg-tertiary">
                  <span>{source.source}</span>
                  {source.published_at ? <span>{source.published_at}</span> : null}
                  {source.pages !== undefined ? <span>{source.pages}p</span> : null}
                </dl>
                {source.quotes && source.quotes.length > 0 ? (
                  <p className="rounded border-l-2 border-accent bg-bg-info/40 px-2 py-1 text-xs italic text-fg-secondary">
                    “{source.quotes[0]}”
                  </p>
                ) : null}
                {source.url ? (
                  <a
                    href={source.url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 self-end text-xs text-fg-info hover:underline"
                  >
                    원본 보기 <ExternalLink className="h-3 w-3" />
                  </a>
                ) : null}
              </div>
            )}
          </div>
        </FloatingPortal>
      ) : null}
    </>
  );
}
