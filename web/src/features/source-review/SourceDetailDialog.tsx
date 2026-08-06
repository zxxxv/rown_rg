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
  /** 검토 게이트가 닫힌 뒤(확정 이후)에는 채택/제외 버튼을 숨긴다 */
  readOnly?: boolean;
  onInclude: (sid: string) => void;
  onExclude: (sid: string) => void;
}

export function SourceDetailDialog({
  source,
  open,
  onOpenChange,
  readOnly = false,
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
              // 본문을 회수하지 못한 출처 — 본문 영역 대신 갖고 있는 요약 정보(출처·
              // 발행·신뢰도)를 보여주고, 검색 근거로 쓰이지 않는다는 사실을 안내한다.
              <section className="flex flex-col gap-2">
                <h3 className="text-xs font-medium uppercase tracking-wide text-fg-tertiary">
                  요약 정보
                </h3>
                <dl className="grid grid-cols-2 gap-3 rounded border border-border bg-bg-secondary p-3 text-sm">
                  <div className="flex flex-col gap-0.5">
                    <dt className="text-xs text-fg-tertiary">출처</dt>
                    <dd className="truncate font-mono text-sm text-fg">{source.source}</dd>
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <dt className="text-xs text-fg-tertiary">발행 시점</dt>
                    <dd className="font-mono text-sm text-fg">
                      {source.published_at ?? "확인 안 됨"}
                    </dd>
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <dt className="text-xs text-fg-tertiary">신뢰도</dt>
                    <dd>
                      <ConfidenceBadge value={source.reliability} />
                    </dd>
                  </div>
                </dl>
                <p className="rounded border border-dashed border-border bg-bg-secondary px-3 py-2.5 text-sm text-fg-secondary">
                  본문을 회수하지 못한 출처입니다 - 검색 근거로 쓰이지 않습니다. 필요한 자료면
                  원본을 확인한 뒤 직접 업로드하거나 추가 검색을 이용하세요.
                </p>
              </section>
            )}
          </div>
        </ScrollArea>

        {readOnly ? null : (
          <DialogFooter className="gap-2">
            <Button variant="secondary" onClick={() => onExclude(source.id)} disabled={isExcluded}>
              제외
            </Button>
            <Button onClick={() => onInclude(source.id)} disabled={isIncluded}>
              채택
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}
