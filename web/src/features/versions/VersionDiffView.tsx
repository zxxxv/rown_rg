import { ChevronDown, ChevronRight, GitCompare, MoveRight, X } from "lucide-react";
import { useMemo, useState } from "react";
import { useVersionDiff, type VersionDiffEntry } from "@/api/versions";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { MarkdownContent } from "@/features/preview/MarkdownContent";
import { cn } from "@/lib/utils";
import { type BlockOp, diffBlocks, diffWords, isOpaqueBlock } from "./textDiff";

// 버전 비교 뷰 - "그때(vN)와 지금"을 절 단위로 훑는다.
// 산문은 인라인 단어 색칠(변경추적 방식 - 좌우 분할은 긴 문단에서 라인 정렬이
// 무의미하다), 전면 재작성 블록·표·차트는 구/신 블록 교체로 보여준다.
// 색만으로 구분하지 않는다: 삭제=취소선+빨강, 추가=밑줄 없는 굵기+초록(접근성).

const STATUS_META: Record<VersionDiffEntry["status"], { label: string; className: string }> = {
  added: { label: "추가", className: "bg-bg-success text-fg-success border-fg-success/40" },
  removed: { label: "삭제", className: "bg-bg-danger text-fg-danger border-fg-danger/40" },
  modified: { label: "수정", className: "bg-bg-warning text-fg-warning border-fg-warning/40" },
  unchanged: { label: "동일", className: "text-fg-tertiary border-border" },
};

function secLabel(s: { chapter_number: number; section_number: number; title: string }): string {
  return `${s.chapter_number}.${s.section_number} ${s.title}`;
}

/** 산문 블록 쌍의 단어 단위 인라인 diff. */
function InlineWordDiff({ before, after }: { before: string; after: string }) {
  // 키 = 종류@원문 누적 오프셋 - 인덱스와 달리 텍스트 위치에 결정적으로 매인다.
  const parts = useMemo(() => {
    let pos = 0;
    return diffWords(before, after).map((op) => {
      const key = `${op.type}@${pos}`;
      pos += op.text.length;
      return { ...op, key };
    });
  }, [before, after]);
  return (
    <p className="whitespace-pre-wrap text-sm leading-7 text-fg">
      {parts.map((op) =>
        op.type === "same" ? (
          <span key={op.key}>{op.text}</span>
        ) : op.type === "del" ? (
          <del key={op.key} className="rounded-sm bg-bg-danger px-0.5 text-fg-danger">
            {op.text}
          </del>
        ) : (
          <ins
            key={op.key}
            className="rounded-sm bg-bg-success px-0.5 font-medium text-fg-success no-underline"
          >
            {op.text}
          </ins>
        ),
      )}
    </p>
  );
}

/** 수정된 절 본문 - 블록 정렬 후 블록별로 렌더. */
function ModifiedBody({ before, after }: { before: string; after: string }) {
  const ops = useMemo(() => {
    let pos = 0;
    return diffBlocks(before, after).map((op) => {
      const key = `${op.type}@${pos}`;
      pos += op.type === "change" ? op.before.length + op.after.length : op.text.length;
      return { op, key };
    });
  }, [before, after]);
  return (
    <div className="flex flex-col gap-3">
      {ops.map(({ op, key }) => (
        <DiffBlock key={key} op={op} />
      ))}
    </div>
  );
}

function DiffBlock({ op }: { op: BlockOp }) {
  if (op.type === "same") {
    return (
      <div className="opacity-70">
        <MarkdownContent content={op.text} />
      </div>
    );
  }
  if (op.type === "add") {
    return (
      <div className="rounded border-l-2 border-fg-success bg-bg-success/40 px-3 py-1">
        <MarkdownContent content={op.text} />
      </div>
    );
  }
  if (op.type === "del") {
    return (
      <div className="rounded border-l-2 border-fg-danger bg-bg-danger/40 px-3 py-1 opacity-80 [&_p]:line-through">
        <MarkdownContent content={op.text} />
      </div>
    );
  }
  // change 쌍: 산문이면 단어 색칠, 표·차트 펜스면 구/신 블록 교체.
  if (isOpaqueBlock(op.before) || isOpaqueBlock(op.after)) {
    return (
      <div className="flex flex-col gap-2">
        <details className="rounded border border-fg-danger/30 bg-bg-danger/30 px-3 py-1">
          <summary className="cursor-pointer select-none text-xs text-fg-danger">
            이전 내용 보기 (교체됨)
          </summary>
          <MarkdownContent content={op.before} />
        </details>
        <div className="rounded border-l-2 border-fg-success bg-bg-success/40 px-3 py-1">
          <MarkdownContent content={op.after} />
        </div>
      </div>
    );
  }
  return <InlineWordDiff before={op.before} after={op.after} />;
}

function EntryCard({ entry }: { entry: VersionDiffEntry }) {
  // 동일 절은 접힌 한 줄, 나머지는 펼침으로 시작 - 바뀐 것부터 읽힌다.
  const [open, setOpen] = useState(entry.status !== "unchanged");
  const meta = STATUS_META[entry.status];
  const head = entry.target ?? entry.base;
  if (!head) return null;
  return (
    <section
      className={cn(
        "rounded border border-border bg-bg",
        entry.status === "removed" && "border-fg-danger/30",
      )}
    >
      <button
        type="button"
        className="flex w-full flex-wrap items-center gap-2 px-3 py-2 text-left"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        {open ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
        )}
        <span
          className={cn(
            "min-w-0 flex-1 truncate text-sm font-medium text-fg",
            entry.status === "removed" && "line-through opacity-70",
          )}
        >
          {secLabel(head)}
        </span>
        {entry.moved && entry.base && entry.target ? (
          <span className="flex shrink-0 items-center gap-1 text-[11px] text-fg-tertiary">
            {entry.base.chapter_number}.{entry.base.section_number}
            <MoveRight className="h-3 w-3" aria-hidden />
            {entry.target.chapter_number}.{entry.target.section_number}
          </span>
        ) : null}
        <Badge variant="outline" className={cn("shrink-0", meta.className)}>
          {meta.label}
        </Badge>
      </button>
      {open ? (
        <div className="border-t border-border px-4 py-3">
          {entry.status === "modified" && entry.base && entry.target ? (
            <>
              {entry.base.title !== entry.target.title ? (
                <p className="mb-2 text-xs text-fg-secondary">
                  제목: <del className="text-fg-danger">{entry.base.title}</del>{" "}
                  <span className="font-medium text-fg-success">{entry.target.title}</span>
                </p>
              ) : null}
              <ModifiedBody before={entry.base.content} after={entry.target.content} />
            </>
          ) : entry.status === "added" && entry.target ? (
            <div className="rounded border-l-2 border-fg-success bg-bg-success/30 px-3 py-1">
              <MarkdownContent content={entry.target.content} />
            </div>
          ) : entry.status === "removed" && entry.base ? (
            <div className="rounded border-l-2 border-fg-danger bg-bg-danger/30 px-3 py-1 opacity-80">
              <MarkdownContent content={entry.base.content} />
            </div>
          ) : entry.target ? (
            <MarkdownContent content={entry.target.content} />
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

export function VersionDiffView({
  projectId,
  base,
  onClose,
}: {
  projectId: string;
  base: number;
  onClose: () => void;
}) {
  const query = useVersionDiff(projectId, base, null);
  const [showUnchanged, setShowUnchanged] = useState(false);
  const diff = query.data;
  const entries = useMemo(
    () => (diff?.entries ?? []).filter((e) => showUnchanged || e.status !== "unchanged"),
    [diff, showUnchanged],
  );
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 rounded border border-border bg-bg-secondary px-3 py-2">
        <GitCompare className="h-4 w-4 shrink-0 text-fg-secondary" aria-hidden />
        <span className="text-sm font-medium text-fg">v{base} ↔ 현재 본문 비교</span>
        {diff ? (
          <span className="text-xs text-fg-secondary">
            <span className="text-fg-success">추가 {diff.n_added}</span> ·{" "}
            <span className="text-fg-warning">수정 {diff.n_modified}</span> ·{" "}
            <span className="text-fg-danger">삭제 {diff.n_removed}</span> · 동일 {diff.n_unchanged}
          </span>
        ) : null}
        <span className="flex-1" />
        <label className="flex cursor-pointer items-center gap-1.5 text-xs text-fg-secondary">
          <input
            type="checkbox"
            checked={showUnchanged}
            onChange={(e) => setShowUnchanged(e.target.checked)}
            className="h-3.5 w-3.5 accent-accent"
          />
          동일 절도 표시
        </label>
        <Button type="button" variant="ghost" size="sm" className="h-7 px-2" onClick={onClose}>
          <X className="mr-1 h-3.5 w-3.5" aria-hidden />
          비교 닫기
        </Button>
      </div>
      {query.isLoading ? (
        <LoadingSkeleton variant="block" />
      ) : query.isError ? (
        <p className="rounded border border-border bg-bg px-3 py-4 text-sm text-fg-secondary">
          비교를 불러오지 못했습니다 - 잠시 후 다시 시도해 주세요.
        </p>
      ) : entries.length === 0 ? (
        <p className="rounded border border-border bg-bg px-3 py-4 text-sm text-fg-secondary">
          {diff && diff.n_unchanged > 0
            ? "바뀐 절이 없습니다 - 현재 본문이 이 버전과 같습니다."
            : "비교할 내용이 없습니다."}
        </p>
      ) : (
        entries.map((e) => <EntryCard key={e.section_id} entry={e} />)
      )}
    </div>
  );
}
