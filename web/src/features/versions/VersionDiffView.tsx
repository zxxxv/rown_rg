import {
  ChevronDown,
  ChevronRight,
  Columns2,
  GitCompare,
  MoveRight,
  Rows3,
  Undo2,
  X,
} from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";
import { useRestoreSection, useVersionDiff, type VersionDiffEntry } from "@/api/versions";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { MarkdownContent } from "@/features/preview/MarkdownContent";
import { cn } from "@/lib/utils";
import { type BlockOp, type DiffOp, diffBlocks, diffWords, isOpaqueBlock } from "./textDiff";

// 버전 비교 뷰 - "그때(vN)와 지금"을 절 단위로 훑는다.
//
// 좌우 분할이 기본이다(2026-08-27 사용자 결정). 원래는 인라인 단어 색칠뿐이었고
// 그 이유가 "좌우 분할은 긴 문단에서 라인 정렬이 무의미하다"였는데, 이미 블록(빈 줄)
// 단위로 먼저 맞추고 있어서(textDiff.diffBlocks) 그 반대 근거가 사라졌다 - 정렬
// 단위가 '행'이 아니라 '문단'이라 좌우로 놓아도 어긋나지 않는다.
// 좌우 칸 **안에서는** 단어 색칠을 그대로 유지한다(GitHub split view와 같은 규약):
// 왼쪽은 삭제된 말만, 오른쪽은 더해진 말만 칠한다. 그래서 한쪽만 읽어도 그 시점
// 본문이 온전한 글로 읽히고, 색만 훑으면 무엇이 달라졌는지 보인다.
// 국소 수정(몇 단어)에는 인라인이 여전히 빠르므로 토글로 남긴다.
// 색만으로 구분하지 않는다: 삭제=취소선+빨강, 추가=밑줄 없는 굵기+초록(접근성).

const STATUS_META: Record<VersionDiffEntry["status"], { label: string; className: string }> = {
  added: { label: "추가", className: "bg-bg-success text-fg-success border-fg-success/40" },
  removed: { label: "삭제", className: "bg-bg-danger text-fg-danger border-fg-danger/40" },
  modified: { label: "수정", className: "bg-bg-warning text-fg-warning border-fg-warning/40" },
  unchanged: { label: "동일", className: "text-fg-tertiary border-border" },
};

const STATUS_DOT: Record<VersionDiffEntry["status"], string> = {
  added: "bg-fg-success",
  removed: "bg-fg-danger",
  modified: "bg-fg-warning",
  unchanged: "bg-border",
};

function secLabel(s: { chapter_number: number; section_number: number; title: string }): string {
  return `${s.chapter_number}.${s.section_number} ${s.title}`;
}

/** 이 절의 diff 화면 앵커 - 목차에서 눌러 여기로 온다. */
function anchorId(sectionId: string): string {
  return `diff-sec-${sectionId}`;
}

// ─── 단어 색칠 ───

/** 한쪽 칸의 본문 - 자기 쪽 변경만 칠하고 반대쪽 변경은 아예 그리지 않는다.
 *  그래야 왼쪽은 '그때 본문', 오른쪽은 '지금 본문'으로 각각 온전히 읽힌다. */
function SideText({ parts, side }: { parts: DiffOp[]; side: "left" | "right" }) {
  let pos = 0;
  return (
    <p className="whitespace-pre-wrap text-sm leading-7 text-fg">
      {parts.map((op) => {
        const key = `${op.type}@${pos}`;
        pos += op.text.length;
        if (op.type === "same") return <span key={key}>{op.text}</span>;
        if (op.type === "del")
          return side === "left" ? (
            <del key={key} className="rounded-sm bg-bg-danger px-0.5 text-fg-danger">
              {op.text}
            </del>
          ) : null;
        return side === "right" ? (
          <ins
            key={key}
            className="rounded-sm bg-bg-success px-0.5 font-medium text-fg-success no-underline"
          >
            {op.text}
          </ins>
        ) : null;
      })}
    </p>
  );
}

/** 산문 블록 쌍의 단어 단위 인라인 diff(한 줄에 구·신을 섞어 보여주는 변경추적 방식). */
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

// ─── 인라인 모드 ───

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

// ─── 좌우 분할 모드 ───

/** 좌우 한 줄(= 블록 하나). 빈 칸은 "이쪽엔 없던 대목"이라 회색 빗금 대신 조용히 비운다. */
function SplitRow({ op }: { op: BlockOp }) {
  // change 쌍의 단어 diff는 한 번만 계산해 양쪽이 나눠 쓴다(칸마다 돌리면 두 배 든다).
  const parts = useMemo(() => (op.type === "change" ? diffWords(op.before, op.after) : null), [op]);
  const opaque = op.type === "change" && (isOpaqueBlock(op.before) || isOpaqueBlock(op.after));

  const left =
    op.type === "same" ? (
      <MarkdownContent content={op.text} />
    ) : op.type === "del" ? (
      <div className="[&_p]:line-through">
        <MarkdownContent content={op.text} />
      </div>
    ) : op.type === "change" ? (
      opaque ? (
        <MarkdownContent content={op.before} />
      ) : (
        <SideText parts={parts ?? []} side="left" />
      )
    ) : null;

  const right =
    op.type === "same" ? (
      <MarkdownContent content={op.text} />
    ) : op.type === "add" ? (
      <MarkdownContent content={op.text} />
    ) : op.type === "change" ? (
      opaque ? (
        <MarkdownContent content={op.after} />
      ) : (
        <SideText parts={parts ?? []} side="right" />
      )
    ) : null;

  // 바뀐 문단(change)에는 칸 배경을 깔지 않는다 - 옅은 빨강/초록을 좌우에 깔아 봤더니
  // 두 색이 거의 같은 베이지로 보여 좌우를 가르지도 못하면서 단어 색칠만 묻혔다
  // (2026-08-27 육안 확인). 그 줄이 바뀐 자리라는 건 단어 색이 이미 말한다.
  // 통째 추가·삭제 블록만 칠하되, 색만으로 알리지 않도록 세로 띠를 함께 준다.
  const tint = (side: "left" | "right") => {
    if (op.type === "same") return "opacity-70";
    if (op.type === "del") return side === "left" ? "bg-bg-danger border-l-2 border-fg-danger" : "";
    if (op.type === "add")
      return side === "right" ? "bg-bg-success border-l-2 border-fg-success" : "";
    return "";
  };

  return (
    <div className="grid grid-cols-1 gap-px bg-border md:grid-cols-2">
      <div className={cn("min-w-0 bg-bg px-3 py-2", tint("left"))}>
        {left ?? <span className="text-xs text-fg-tertiary">- 이 버전엔 없던 대목</span>}
      </div>
      <div className={cn("min-w-0 bg-bg px-3 py-2", tint("right"))}>
        {right ?? <span className="text-xs text-fg-tertiary">- 이후 빠진 대목</span>}
      </div>
    </div>
  );
}

/** 좌우 분할 본문 - 블록 정렬이 곧 행 정렬이다. */
function SplitBody({
  before,
  after,
  leftLabel,
  rightLabel,
}: {
  before: string;
  after: string;
  leftLabel: string;
  rightLabel: string;
}) {
  const ops = useMemo(() => {
    let pos = 0;
    return diffBlocks(before, after).map((op) => {
      const key = `${op.type}@${pos}`;
      pos += op.type === "change" ? op.before.length + op.after.length : op.text.length;
      return { op, key };
    });
  }, [before, after]);
  return (
    <div className="overflow-hidden rounded border border-border">
      {/* 어느 쪽이 어느 버전인지 - 절마다 붙인다. 긴 절을 읽어 내려가다 좌우가
          헷갈리는 순간이 실제로 온다(위쪽 도구줄 하나로는 부족하다). */}
      <div className="sticky top-0 z-10 grid grid-cols-1 gap-px border-b border-border bg-border md:grid-cols-2">
        <div className="bg-bg-secondary px-3 py-1.5 text-xs font-medium text-fg-secondary">
          {leftLabel}
        </div>
        <div className="bg-bg-secondary px-3 py-1.5 text-xs font-medium text-fg-secondary">
          {rightLabel}
        </div>
      </div>
      <div className="flex flex-col gap-px bg-border">
        {ops.map(({ op, key }) => (
          <SplitRow key={key} op={op} />
        ))}
      </div>
    </div>
  );
}

/** 한쪽에만 있는 절(추가·삭제)도 같은 좌우 틀로 - 어느 쪽에 생겼는지가 곧 답이다. */
function SplitOneSided({
  content,
  side,
  leftLabel,
  rightLabel,
}: {
  content: string;
  side: "left" | "right";
  leftLabel: string;
  rightLabel: string;
}) {
  return (
    <div className="overflow-hidden rounded border border-border">
      <div className="grid grid-cols-1 gap-px border-b border-border bg-border md:grid-cols-2">
        <div className="bg-bg-secondary px-3 py-1.5 text-xs font-medium text-fg-secondary">
          {leftLabel}
        </div>
        <div className="bg-bg-secondary px-3 py-1.5 text-xs font-medium text-fg-secondary">
          {rightLabel}
        </div>
      </div>
      <div className="grid grid-cols-1 gap-px bg-border md:grid-cols-2">
        <div
          className={cn(
            "min-w-0 bg-bg px-3 py-2",
            side === "left" && "bg-bg-danger border-l-2 border-fg-danger",
          )}
        >
          {side === "left" ? (
            <MarkdownContent content={content} />
          ) : (
            <span className="text-xs text-fg-tertiary">- 이 버전엔 없던 절</span>
          )}
        </div>
        <div
          className={cn(
            "min-w-0 bg-bg px-3 py-2",
            side === "right" && "bg-bg-success border-l-2 border-fg-success",
          )}
        >
          {side === "right" ? (
            <MarkdownContent content={content} />
          ) : (
            <span className="text-xs text-fg-tertiary">- 이후 빠진 절</span>
          )}
        </div>
      </div>
    </div>
  );
}

// ─── 목차 ───

/** 비교 전용 목차 - 본문 트리를 쓰지 않는 이유: 삭제된 절은 지금 목차에 없다.
 *  이 비교에 등장하는 절 전부(삭제분 포함)를 장으로 묶어 보여줘야 훑을 수 있다. */
function DiffOutline({
  entries,
  activeId,
  onJump,
}: {
  entries: VersionDiffEntry[];
  activeId: string | null;
  onJump: (sectionId: string) => void;
}) {
  const chapters = useMemo(() => {
    const groups = new Map<number, { title: string; items: VersionDiffEntry[] }>();
    for (const e of entries) {
      const head = e.target ?? e.base;
      if (!head) continue;
      const g = groups.get(head.chapter_number) ?? {
        title: head.chapter_title || `${head.chapter_number}장`,
        items: [],
      };
      g.items.push(e);
      groups.set(head.chapter_number, g);
    }
    return [...groups.entries()].sort((a, b) => a[0] - b[0]);
  }, [entries]);

  return (
    <nav className="flex flex-col gap-3 p-3" aria-label="비교 목차">
      {chapters.map(([n, g]) => (
        <div key={n} className="flex flex-col gap-0.5">
          <p className="px-1 text-xs font-medium text-fg-secondary">
            {n}장 {g.title}
          </p>
          {g.items.map((e) => {
            const head = e.target ?? e.base;
            if (!head) return null;
            return (
              <button
                key={e.section_id}
                type="button"
                onClick={() => onJump(e.section_id)}
                className={cn(
                  "flex items-center gap-1.5 rounded px-1.5 py-1 text-left text-xs hover:bg-bg-secondary",
                  activeId === e.section_id
                    ? "bg-bg-secondary font-medium text-fg"
                    : "text-fg-secondary",
                )}
              >
                <span
                  className={cn("h-1.5 w-1.5 shrink-0 rounded-full", STATUS_DOT[e.status])}
                  aria-hidden
                />
                <span className="shrink-0 font-mono text-[11px] text-fg-tertiary">
                  {head.chapter_number}.{head.section_number}
                </span>
                <span className={cn("min-w-0 truncate", e.status === "removed" && "line-through")}>
                  {head.title}
                </span>
              </button>
            );
          })}
        </div>
      ))}
    </nav>
  );
}

// ─── 절 카드 ───

function EntryCard({
  entry,
  mode,
  leftLabel,
  rightLabel,
  onRestore,
  restoring,
}: {
  entry: VersionDiffEntry;
  mode: "split" | "inline";
  leftLabel: string;
  rightLabel: string;
  /** 이 절만 비교 대상 버전으로 되돌린다 - 바뀐 절에만 준다. */
  onRestore?: () => void;
  restoring?: boolean;
}) {
  // 동일 절은 접힌 한 줄, 나머지는 펼침으로 시작 - 바뀐 것부터 읽힌다.
  const [open, setOpen] = useState(entry.status !== "unchanged");
  const meta = STATUS_META[entry.status];
  const head = entry.target ?? entry.base;
  if (!head) return null;
  const split = mode === "split";
  return (
    <section
      id={anchorId(entry.section_id)}
      // 목차에서 뛰어왔을 때 절 제목이 도구줄에 가리지 않게 - 스크롤 여백을 준다.
      className={cn(
        "scroll-mt-20 rounded border border-border bg-bg",
        entry.status === "removed" && "border-fg-danger/30",
      )}
    >
      {onRestore ? (
        <div className="flex justify-end px-3 pt-2">
          <Button
            variant="ghost"
            size="sm"
            className="h-6 px-2 text-xs"
            disabled={restoring}
            onClick={onRestore}
            title="이 절만 그때 내용으로 되돌립니다 - 다른 절은 그대로입니다"
          >
            <Undo2 className="mr-1 h-3.5 w-3.5" />이 절 되돌리기
          </Button>
        </div>
      ) : null}
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
              {split ? (
                <SplitBody
                  before={entry.base.content}
                  after={entry.target.content}
                  leftLabel={leftLabel}
                  rightLabel={rightLabel}
                />
              ) : (
                <ModifiedBody before={entry.base.content} after={entry.target.content} />
              )}
            </>
          ) : entry.status === "added" && entry.target ? (
            split ? (
              <SplitOneSided
                content={entry.target.content}
                side="right"
                leftLabel={leftLabel}
                rightLabel={rightLabel}
              />
            ) : (
              <div className="rounded border-l-2 border-fg-success bg-bg-success/30 px-3 py-1">
                <MarkdownContent content={entry.target.content} />
              </div>
            )
          ) : entry.status === "removed" && entry.base ? (
            split ? (
              <SplitOneSided
                content={entry.base.content}
                side="left"
                leftLabel={leftLabel}
                rightLabel={rightLabel}
              />
            ) : (
              <div className="rounded border-l-2 border-fg-danger bg-bg-danger/30 px-3 py-1 opacity-80">
                <MarkdownContent content={entry.base.content} />
              </div>
            )
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
  target = null,
  onClose,
}: {
  projectId: string;
  base: number;
  /** null이면 현재 작업 사본과 비교(가장 흔한 용례). 값이 있으면 버전끼리 견준다 */
  target?: number | null;
  onClose: () => void;
}) {
  const query = useVersionDiff(projectId, base, target);
  const restore = useRestoreSection(projectId, base);
  const [showUnchanged, setShowUnchanged] = useState(false);
  const [mode, setMode] = useState<"split" | "inline">("split");
  const [activeId, setActiveId] = useState<string | null>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const diff = query.data;
  const entries = useMemo(
    () => (diff?.entries ?? []).filter((e) => showUnchanged || e.status !== "unchanged"),
    [diff, showUnchanged],
  );
  const leftLabel = `v${base} (그때)`;
  const rightLabel = target === null ? "현재 본문" : `v${target}`;

  const jump = useCallback((sectionId: string) => {
    setActiveId(sectionId);
    document.getElementById(anchorId(sectionId))?.scrollIntoView({ behavior: "smooth" });
  }, []);

  return (
    <div className="flex flex-col gap-3">
      {/* 도구줄은 붙여 둔다 - 긴 비교를 내려가는 동안 무엇과 무엇을 보고 있는지,
          그리고 닫는 문이 계속 손에 닿아야 한다. */}
      <div className="sticky top-0 z-20 flex flex-wrap items-center gap-2 rounded border border-border bg-bg-secondary px-3 py-2">
        <GitCompare className="h-4 w-4 shrink-0 text-fg-secondary" aria-hidden />
        <span className="text-sm font-medium text-fg">
          v{base} ↔ {target === null ? "현재 본문" : `v${target}`} 비교
        </span>
        {diff ? (
          <span className="text-xs text-fg-secondary">
            <span className="text-fg-success">추가 {diff.n_added}</span> ·{" "}
            <span className="text-fg-warning">수정 {diff.n_modified}</span> ·{" "}
            <span className="text-fg-danger">삭제 {diff.n_removed}</span> · 동일 {diff.n_unchanged}
          </span>
        ) : null}
        <span className="flex-1" />
        {/* 국소 수정(몇 단어)은 인라인이 한 번에 읽힌다 - 좌우가 기본이되 길을 남긴다. */}
        <div className="flex shrink-0 items-center overflow-hidden rounded border border-border">
          <button
            type="button"
            onClick={() => setMode("split")}
            aria-pressed={mode === "split"}
            title="좌우로 나란히 놓고 봅니다"
            className={cn(
              "flex items-center gap-1 px-2 py-1 text-xs",
              mode === "split" ? "bg-bg-info text-fg-info" : "text-fg-secondary hover:bg-bg",
            )}
          >
            <Columns2 className="h-3.5 w-3.5" aria-hidden />
            좌우
          </button>
          <button
            type="button"
            onClick={() => setMode("inline")}
            aria-pressed={mode === "inline"}
            title="한 흐름에 구·신을 섞어 표시합니다(변경추적 방식)"
            className={cn(
              "flex items-center gap-1 border-l border-border px-2 py-1 text-xs",
              mode === "inline" ? "bg-bg-info text-fg-info" : "text-fg-secondary hover:bg-bg",
            )}
          >
            <Rows3 className="h-3.5 w-3.5" aria-hidden />한 줄
          </button>
        </div>
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
        // 비교 중에도 목차를 둔다 - 어느 절을 보고 있는지, 다음에 볼 절이 무엇인지가
        // 본문 화면과 똑같이 필요하다(2026-08-27 지적). 본문 트리와 폭 규칙을 맞춘다.
        <div className="grid grid-cols-1 gap-4 min-[1200px]:grid-cols-[240px_minmax(0,1fr)]">
          <aside className="self-start rounded border border-border bg-bg min-[1200px]:sticky min-[1200px]:top-16">
            <DiffOutline entries={entries} activeId={activeId} onJump={jump} />
          </aside>
          <div ref={bodyRef} className="flex min-w-0 flex-col gap-3">
            {entries.map((e) => (
              <EntryCard
                key={e.section_id}
                entry={e}
                mode={mode}
                leftLabel={leftLabel}
                rightLabel={rightLabel}
                // 되돌릴 대상이 있는 절만 - 추가된 절은 그 버전에 없어서 되돌릴 게 없다.
                // 되돌리기는 언제나 **base(왼쪽·오래된 쪽)** 내용으로 간다.
                onRestore={
                  e.base && e.status !== "unchanged"
                    ? () => restore.mutate(e.section_id)
                    : undefined
                }
                restoring={restore.isPending}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
