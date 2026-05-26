import { ExternalLink } from "lucide-react";
import type { Source } from "@/api/types";
import { ConfidenceBadge } from "@/components/data-display/ConfidenceBadge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";

export interface SourceDetailDialogProps {
  source: Source | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onInclude: (sid: string) => void;
  onExclude: (sid: string) => void;
}

export function SourceDetailDialog({
  source,
  open,
  onOpenChange,
  onInclude,
  onExclude,
}: SourceDetailDialogProps) {
  if (!source) return null;
  const isIncluded = source.is_included === true;
  const isExcluded = source.is_included === false;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle className="text-left">{source.title}</DialogTitle>
          <DialogDescription className="flex flex-wrap items-center gap-2 text-left">
            <span className="font-mono text-xs text-fg-secondary">{source.source}</span>
            {source.published_at ? (
              <span className="font-mono text-xs text-fg-tertiary">{source.published_at}</span>
            ) : null}
            {source.pages !== undefined ? (
              <span className="font-mono text-xs text-fg-tertiary">{source.pages}p</span>
            ) : null}
            <ConfidenceBadge value={source.reliability} />
            {source.url ? (
              <a
                href={source.url}
                target="_blank"
                rel="noreferrer"
                className="ml-auto inline-flex items-center gap-1 text-xs text-fg-info hover:underline"
              >
                원본 열기 <ExternalLink className="h-3 w-3" />
              </a>
            ) : null}
          </DialogDescription>
        </DialogHeader>

        <ScrollArea className="max-h-[60vh] pr-2">
          <div className="flex flex-col gap-4">
            <section>
              <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-tertiary">
                요약
              </h3>
              <p className="text-sm leading-relaxed text-fg">{source.summary}</p>
            </section>

            {source.quotes && source.quotes.length > 0 ? (
              <section>
                <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-fg-tertiary">
                  핵심 인용구
                </h3>
                <ul className="flex flex-col gap-2">
                  {source.quotes.map((q) => (
                    <li
                      key={q}
                      className="rounded border-l-2 border-accent bg-bg-info/40 px-3 py-2 text-sm italic text-fg"
                    >
                      “{q}”
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}

            <section>
              <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-tertiary">
                본문 첫 페이지 미리보기
              </h3>
              <div className="rounded border border-border bg-bg-secondary p-4 text-sm leading-relaxed text-fg-secondary">
                {source.preview ??
                  "본문 미리보기는 인덱싱 단계에서 추출됩니다. 현재는 자료의 자동 요약과 핵심 인용구만 표시됩니다."}
              </div>
            </section>
          </div>
        </ScrollArea>

        <DialogFooter className="gap-2">
          <Button variant="secondary" onClick={() => onExclude(source.id)} disabled={isExcluded}>
            제외
          </Button>
          <Button onClick={() => onInclude(source.id)} disabled={isIncluded}>
            채택
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
