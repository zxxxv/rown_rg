import type { ReactNode } from "react";
import { ConfidenceBadge } from "@/components/data-display/ConfidenceBadge";
import { cn } from "@/lib/utils";

export interface SourceCardProps {
  title: string;
  source: string;
  publishedAt?: string;
  pages?: number;
  reliability?: number;
  summary?: string;
  onClick?: () => void;
  actions?: ReactNode;
  className?: string;
}

export function SourceCard({
  title,
  source,
  publishedAt,
  pages,
  reliability,
  summary,
  onClick,
  actions,
  className,
}: SourceCardProps) {
  const interactive = Boolean(onClick);
  return (
    <article
      className={cn(
        "flex flex-col gap-3 rounded border border-border bg-bg p-5 transition-colors",
        interactive && "cursor-pointer hover:border-border-strong hover:bg-bg-secondary",
        className,
      )}
      onClick={onClick}
      onKeyDown={(e) => {
        if (!onClick) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
    >
      <header className="flex items-start justify-between gap-3">
        <h3 className="line-clamp-2 text-base font-semibold text-fg">{title}</h3>
        {reliability !== undefined ? <ConfidenceBadge value={reliability} /> : null}
      </header>

      <dl className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-xs text-fg-tertiary">
        <div className="flex items-center gap-1">
          <dt className="sr-only">출처</dt>
          <dd>{source}</dd>
        </div>
        {publishedAt ? (
          <div className="flex items-center gap-1">
            <dt className="sr-only">발행일</dt>
            <dd>{publishedAt}</dd>
          </div>
        ) : null}
        {pages !== undefined ? (
          <div className="flex items-center gap-1">
            <dt className="sr-only">페이지</dt>
            <dd>{pages}p</dd>
          </div>
        ) : null}
      </dl>

      {summary ? <p className="line-clamp-3 text-sm text-fg-secondary">{summary}</p> : null}

      {actions ? (
        <div
          role="toolbar"
          aria-label="자료 액션"
          className="flex flex-wrap items-center gap-2 pt-2"
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => e.stopPropagation()}
        >
          {actions}
        </div>
      ) : null}
    </article>
  );
}
