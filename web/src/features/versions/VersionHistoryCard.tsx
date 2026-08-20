import { ChevronDown, ChevronRight, Download, GitCompare, History } from "lucide-react";
import { useState } from "react";
import { type ReportVersion, useReportVersions } from "@/api/versions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { env } from "@/env";
import { useDownload } from "@/features/export/useDownload";

// 버전 기록 - 커밋 로그처럼 읽힌다: 언제·왜(완성/재개 보존)·규모.
// 스냅샷은 서버가 자동으로 쌓는다(조립 완성·재개 직전). 여기서는 보기·비교·다운로드만.

const REASON_LABEL: Record<ReportVersion["reason"], string> = {
  assemble: "완성본",
  reopen: "재개 전 보존",
  manual: "수동 저장",
};

function fmtDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(
    d.getMinutes(),
  ).padStart(2, "0")}`;
}

export function VersionHistoryCard({
  projectId,
  projectTitle,
  onCompare,
}: {
  projectId: string;
  projectTitle: string;
  /** "현재와 비교" 클릭 - 부모(작업공간)가 비교 뷰로 전환한다 */
  onCompare: (baseVersion: number) => void;
}) {
  const query = useReportVersions(projectId);
  const [open, setOpen] = useState(false);
  const { download, pending } = useDownload();
  const versions = query.data ?? [];
  if (versions.length === 0) return null;

  const apiBase = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return (
    <section className="rounded border border-border bg-bg">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        {open ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
        )}
        <History className="h-4 w-4 shrink-0 text-fg-secondary" aria-hidden />
        <span className="text-sm font-medium text-fg">버전 기록</span>
        <span className="text-xs text-fg-tertiary">
          {versions.length}개 · 최신 v{versions[0].version_no} ({fmtDate(versions[0].created_at)})
        </span>
      </button>
      {open ? (
        <ul className="flex flex-col border-t border-border">
          {versions.map((v) => (
            <li
              key={v.version_no}
              className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2 text-sm last:border-b-0"
            >
              <Badge variant="outline" className="shrink-0 font-mono">
                v{v.version_no}
              </Badge>
              <span className="shrink-0 text-xs text-fg-secondary">{REASON_LABEL[v.reason]}</span>
              <span className="shrink-0 text-xs text-fg-tertiary">{fmtDate(v.created_at)}</span>
              <span className="min-w-0 flex-1 truncate text-xs text-fg-tertiary">
                {v.n_sections}절 · {v.total_chars.toLocaleString()}자
              </span>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-xs"
                onClick={() => onCompare(v.version_no)}
                title="이 버전과 현재 본문의 차이를 절 단위로 봅니다"
              >
                <GitCompare className="mr-1 h-3.5 w-3.5" aria-hidden />
                현재와 비교
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 px-2 text-xs"
                disabled={pending}
                onClick={() =>
                  void download({
                    url: `${apiBase}/projects/${projectId}/versions/${v.version_no}/download`,
                    filename: `${projectTitle}.v${v.version_no}.hwpx`,
                    label: `v${v.version_no} HWPX`,
                  })
                }
              >
                <Download className="mr-1 h-3.5 w-3.5" aria-hidden />
                HWPX
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
