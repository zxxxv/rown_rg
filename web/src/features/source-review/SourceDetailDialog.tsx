import { ExternalLink } from "lucide-react";
import type { Source } from "@/api/types";
import { ConfidenceBadge } from "@/components/data-display/ConfidenceBadge";
import { Badge } from "@/components/ui/badge";
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
            {source.matched_sections && source.matched_sections.length > 0 ? (
              <section>
                <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-tertiary">
                  관련 목차
                </h3>
                <div className="flex flex-wrap gap-1.5">
                  {source.matched_sections.map((sec) => (
                    <Badge key={sec} variant="outline" className="text-xs">
                      {sec}
                    </Badge>
                  ))}
                </div>
              </section>
            ) : null}

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

            {source.preview ? (
              <section>
                <h3 className="mb-1 text-xs font-medium uppercase tracking-wide text-fg-tertiary">
                  본문 미리보기
                </h3>
                <div className="rounded border border-border bg-bg-secondary p-4 text-sm leading-relaxed text-fg-secondary">
                  {source.preview}
                </div>
              </section>
            ) : (
              // 본문을 회수하지 못한 출처 — 있지도 않은 본문 영역을 그리지 않고,
              // 검색 근거로 쓰이지 않는다는 사실과 확인 경로(원본 링크)만 안내한다.
              <p className="rounded border border-dashed border-border bg-bg-secondary px-3 py-2.5 text-sm text-fg-secondary">
                본문을 회수하지 못한 출처입니다 — 검색 근거로 쓰이지 않습니다. 내용은 상단의 원본
                링크에서 직접 확인하세요.
              </p>
            )}
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
