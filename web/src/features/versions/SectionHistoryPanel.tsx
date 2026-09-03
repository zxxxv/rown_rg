import { ChevronDown, ChevronRight, Eye, Undo2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { type SectionHistoryEntry, useRestoreSection, useSectionHistory } from "@/api/versions";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { MarkdownContent } from "@/features/preview/MarkdownContent";
import { fmtKstCompact } from "@/lib/datetime";
import { cn } from "@/lib/utils";
import { reasonLabel } from "./reasons";
import { diffWords } from "./textDiff";

// 이 절 하나의 이력 - 절 화면에서 바로 여는 문.
// 버전 기록 카드가 "문서가 어떻게 흘러왔나"라면 여기는 "이 절이 어떻게 바뀌었나"다.
// 같은 내용이 이어진 구간은 서버가 한 칸으로 접어 준다(안 접으면 남의 절을 고친
// 버전까지 이 절 이력에 한 줄씩 쌓인다).

// 표시는 KST 고정(fmtKstCompact) - 버전 이력만 브라우저 로컬 시간이라 화면마다
// 시각이 달랐다.

/** 지금 본문과 이 시점 본문의 단어 단위 차이 - 몇 자가 늘고 줄었는지 한 줄로. */
function changeSummary(before: string, after: string): string {
  let added = 0;
  let removed = 0;
  for (const op of diffWords(before, after)) {
    if (op.type === "add") added += op.text.length;
    if (op.type === "del") removed += op.text.length;
  }
  if (!added && !removed) return "지금과 같은 내용";
  const parts: string[] = [];
  if (removed) parts.push(`이 시점에만 있던 ${removed.toLocaleString()}자`);
  if (added) parts.push(`그 뒤 더해진 ${added.toLocaleString()}자`);
  return parts.join(" · ");
}

function Entry({
  projectId,
  sectionId,
  entry,
  current,
  editable,
  onPeek,
}: {
  projectId: string;
  sectionId: string;
  entry: SectionHistoryEntry;
  current: string;
  editable: boolean;
  /** 본문 자리에서 이 시점 원고를 편다 - 접힌 칸을 펴서 좁게 읽는 것보다 빠르다 */
  onPeek: (entry: SectionHistoryEntry | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const restore = useRestoreSection(projectId, entry.version_no);
  const range =
    entry.until_version === entry.version_no
      ? `v${entry.version_no}`
      : `v${entry.version_no}~v${entry.until_version}`;

  const onRestore = () => {
    restore.mutate(sectionId, {
      onSuccess: () => {
        // 되돌린 내용이 곧 본문이다 - 엿보기를 켜 둔 채면 같은 글이 두 겹으로 보인다.
        onPeek(null);
        toast.success(`v${entry.version_no}의 내용으로 되돌렸습니다`, {
          description: "이 절만 바뀌었고, 되돌린 직후도 버전으로 남아 다시 되돌릴 수 있습니다.",
        });
      },
      onError: (err: unknown) =>
        toast.error("되돌리기에 실패했습니다", {
          description: err instanceof ApiError ? err.message : "잠시 후 다시 시도해 주세요.",
        }),
    });
  };

  return (
    <li
      className={cn(
        "border-b border-border last:border-b-0",
        entry.is_current && "bg-bg-success/20",
      )}
    >
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          {open ? (
            <ChevronDown className="h-3.5 w-3.5 shrink-0 text-fg-tertiary" aria-hidden />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 shrink-0 text-fg-tertiary" aria-hidden />
          )}
          <Badge variant="outline" className="shrink-0 font-mono text-[11px]">
            {range}
          </Badge>
          <span className="shrink-0 text-xs text-fg-secondary">{reasonLabel(entry.reason)}</span>
          <span className="shrink-0 text-xs text-fg-tertiary">
            {fmtKstCompact(entry.created_at)}
          </span>
          <span className="min-w-0 flex-1 truncate text-xs text-fg-tertiary">
            {entry.char_count.toLocaleString()}자 · {changeSummary(entry.content, current)}
          </span>
        </button>
        {entry.is_current ? (
          <Badge
            variant="outline"
            className="shrink-0 border-fg-success/40 bg-bg-success text-fg-success"
          >
            지금 본문
          </Badge>
        ) : (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 shrink-0 px-2 text-xs"
            onClick={() => onPeek(entry)}
            title="이 시점 원고를 본문 자리에 펴서 읽습니다 - 되돌리는 것은 아닙니다"
          >
            <Eye className="mr-1 h-3.5 w-3.5" aria-hidden />
            본문에서 보기
          </Button>
        )}
        {!entry.is_current && editable ? (
          <Button
            variant="ghost"
            size="sm"
            className="h-7 shrink-0 px-2 text-xs"
            disabled={restore.isPending}
            onClick={onRestore}
            title="이 절만 그때 내용으로 되돌립니다 - 다른 절은 그대로입니다"
          >
            <Undo2 className="mr-1 h-3.5 w-3.5" aria-hidden />
            {restore.isPending ? "되돌리는 중…" : "이 내용으로"}
          </Button>
        ) : null}
      </div>
      {open ? (
        <div className="border-t border-border bg-bg px-4 py-3">
          {entry.content.trim() ? (
            <MarkdownContent content={entry.content} />
          ) : (
            <p className="text-xs text-fg-tertiary">이 시점에는 본문이 비어 있었습니다.</p>
          )}
        </div>
      ) : null}
    </li>
  );
}

export function SectionHistoryPanel({
  projectId,
  sectionId,
  current,
  editable,
  onPeek,
}: {
  projectId: string;
  sectionId: string;
  /** 지금 화면의 본문 - 어느 칸이 현재와 같은지, 무엇이 달라졌는지 여기서 잰다 */
  current: string;
  editable: boolean;
  onPeek: (entry: SectionHistoryEntry | null) => void;
}) {
  const query = useSectionHistory(projectId, sectionId);
  if (query.isLoading) {
    return (
      <div className="border-b border-border px-6 py-3">
        <LoadingSkeleton variant="block" />
      </div>
    );
  }
  const entries = query.data?.entries ?? [];
  return (
    <div className="border-b border-border bg-bg-secondary px-6 py-3">
      {query.isError ? (
        <p className="text-xs text-fg-secondary">이력을 불러오지 못했습니다.</p>
      ) : entries.length === 0 ? (
        // 버전이 쌓이기 전(첫 완성 전)이거나 이 절이 아직 한 번도 얼려지지 않았다.
        <p className="text-xs text-fg-secondary">
          아직 이 절의 이력이 없습니다 - 보고서가 완성되거나 버전을 저장하면 이 절의 그때 내용을
          여기서 되살릴 수 있습니다.
        </p>
      ) : (
        <>
          <p className="mb-2 text-xs text-fg-secondary">
            이 절이 달라진 시점 {entries.length}개 - 같은 내용이 이어진 구간은 한 칸으로 묶었습니다.
          </p>
          <ul className="flex flex-col rounded border border-border bg-bg">
            {entries.map((e) => (
              <Entry
                key={`${e.version_no}-${e.until_version}`}
                projectId={projectId}
                sectionId={sectionId}
                entry={e}
                current={current}
                editable={editable}
                onPeek={onPeek}
              />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
