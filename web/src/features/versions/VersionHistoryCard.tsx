import {
  ChevronDown,
  ChevronRight,
  Download,
  GitCompare,
  History,
  Save,
  Scale,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useReportVersions } from "@/api/versions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { env } from "@/env";
import { useDownload } from "@/features/export/useDownload";
import { cn } from "@/lib/utils";
import { isMilestoneReason, reasonLabel } from "./reasons";
import { SaveVersionDialog } from "./SaveVersionDialog";

// 버전 기록 - 커밋 로그처럼 읽힌다: 언제·왜(완성/재개 보존/재작성/확정)·규모.
// 스냅샷은 서버가 자동으로 쌓고(조립·재개·재작성·편집·확정), 수동 저장 버튼이 그 사이를 메운다.
//
// 기본 목록은 **이정표만** 보여준다(reasons.isMilestoneReason). 절을 하나 고칠 때마다
// 자동으로 쌓이는 스냅샷은 되돌리기용 안전망이지 사람이 읽는 이력이 아니라, 다 늘어놓으면
// 35절 보고서 한 바퀴에 수십 줄이 되어 정작 찾는 지점이 묻힌다(2026-08-27).

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
  alwaysOpen = false,
}: {
  projectId: string;
  projectTitle: string;
  /** 시트 안에서는 접는 머리가 필요 없다 - 이미 사람이 열어서 들어온 자리다. */
  alwaysOpen?: boolean;
  /** 비교 열기 - 부모(작업공간)가 비교 뷰로 전환한다. target 없으면 현재 본문과 견준다 */
  onCompare: (baseVersion: number, targetVersion?: number) => void;
}) {
  const query = useReportVersions(projectId);
  // 상태 패널의 '기록 열기'가 ?versions=1을 붙인다 - 카드가 본문 열에 있어서
  // 프롭을 깊게 넘기는 대신 같은 페이지의 URL로 잇는다(compare와 같은 방식).
  const [params, setParams] = useSearchParams();
  const [open, setOpen] = useState(alwaysOpen || params.get("versions") === "1");
  const [showMinor, setShowMinor] = useState(false);
  const [saving, setSaving] = useState(false);
  // 버전끼리 비교하려고 먼저 고른 한쪽. 서버는 처음부터 두 버전 비교를 지원했는데
  // 화면이 '현재와 비교'에만 묶여 있어 v3↔v5를 볼 길이 없었다(2026-08-27).
  // 모드 토글 대신 두 번 클릭으로 끝낸다: 기준 고르기 → 견줄 버전 고르기.
  const [pickBase, setPickBase] = useState<number | null>(null);
  // 초기값만 읽으면 이미 떠 있는 카드는 URL 변화를 못 본다(개요에서 버튼을 누르는
  // 순간이 바로 그 경우다). 열고 나서 파라미터를 **지운다** - 안 지우면 접었다가
  // 다시 눌러도 URL이 그대로라 아무 일도 안 일어난다.
  useEffect(() => {
    if (params.get("versions") !== "1") return;
    setOpen(true);
    const next = new URLSearchParams(params);
    next.delete("versions");
    setParams(next, { replace: true });
  }, [params, setParams]);
  const { download, pending } = useDownload();
  const versions = useMemo(() => query.data ?? [], [query.data]);
  const nMinor = useMemo(
    () => versions.filter((v) => !isMilestoneReason(v.reason)).length,
    [versions],
  );
  // 이정표가 하나도 없는 구간(작성 중 편집만 쌓인 경우)에서 접으면 빈 목록이 된다.
  // 그럴 땐 토글과 무관하게 전부 보여준다 - 빈 화면보다 시끄러운 편이 낫다.
  const showAll = showMinor || nMinor === versions.length;
  const visible = useMemo(
    () => (showAll ? versions : versions.filter((v) => isMilestoneReason(v.reason))),
    [versions, showAll],
  );
  if (versions.length === 0) return null;

  const apiBase = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return (
    <section id="version-history" className="rounded border border-border bg-bg">
      <div className="flex w-full items-center gap-2 px-3 py-2">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          // 시트 안에서는 접을 일이 없다 - 사람이 열어서 들어온 자리다.
          disabled={alwaysOpen}
        >
          {alwaysOpen ? null : open ? (
            <ChevronDown className="h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
          ) : (
            <ChevronRight className="h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
          )}
          {/* 시트가 제목을 이미 달고 있어 여기서 또 말하면 "버전 기록"이 두 번 뜬다. */}
          {alwaysOpen ? null : (
            <>
              <History className="h-4 w-4 shrink-0 text-fg-secondary" aria-hidden />
              <span className="text-sm font-medium text-fg">버전 기록</span>
            </>
          )}
          {/* 목록에 보이는 수(주요 버전)와 실제 마지막 변경을 갈라 말한다 - 그냥
              "6개 · 최신 v6"이라고 하면 접힌 목록에 없는 번호를 가리켜 어긋난다. */}
          <span className="text-xs text-fg-tertiary">
            주요 버전 {versions.length - nMinor}개 · 마지막 변경 v{versions[0].version_no} (
            {fmtDate(versions[0].created_at)})
          </span>
        </button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 shrink-0 px-2 text-xs"
          onClick={() => setSaving(true)}
          title="현재 본문을 이름 붙여 버전으로 남깁니다 - 수동 편집 구간을 보존하는 체크포인트"
        >
          <Save className="mr-1 h-3.5 w-3.5" aria-hidden />
          버전 저장
        </Button>
      </div>
      {open ? (
        <>
          {pickBase !== null ? (
            <div className="flex flex-wrap items-center gap-2 border-t border-border bg-bg-info px-3 py-1.5 text-xs text-fg-info">
              <Scale className="h-3.5 w-3.5 shrink-0" aria-hidden />v{pickBase}을 한쪽으로
              골랐습니다 - 견줄 버전의 '비교'를 누르세요
              <button
                type="button"
                className="ml-auto inline-flex items-center gap-1 text-fg-secondary hover:text-fg"
                onClick={() => setPickBase(null)}
              >
                <X className="h-3.5 w-3.5" aria-hidden />
                고르기 취소
              </button>
            </div>
          ) : null}
          {nMinor > 0 && nMinor < versions.length ? (
            <label className="flex cursor-pointer items-center gap-1.5 border-t border-border px-3 py-1.5 text-xs text-fg-secondary">
              <input
                type="checkbox"
                checked={showMinor}
                onChange={(e) => setShowMinor(e.target.checked)}
                className="h-3.5 w-3.5 accent-accent"
              />
              절 단위 편집까지 보기 ({nMinor}개) - 절을 고칠 때마다 자동으로 남는 되돌리기용
              기록입니다
            </label>
          ) : null}
          <ul className="flex flex-col border-t border-border">
            {visible.map((v) => {
              const minor = !isMilestoneReason(v.reason);
              return (
                <li
                  key={v.version_no}
                  className={cn(
                    "flex flex-wrap items-center gap-2 border-b border-border px-3 py-2 text-sm last:border-b-0",
                    // 펼쳤을 때도 이정표와 잔편집이 한눈에 갈라져 보이게 - 같은 무게로
                    // 늘어놓으면 접어 둔 이유가 없어진다. 배경만으로는 화면에서 거의
                    // 구분이 안 됐다(2026-08-27 CDP 육안 확인) - 들여쓰기를 함께 준다.
                    minor && "bg-bg-secondary pl-9",
                  )}
                >
                  <Badge variant="outline" className="shrink-0 font-mono">
                    v{v.version_no}
                  </Badge>
                  <span
                    className={cn(
                      "shrink-0 text-xs",
                      minor ? "text-fg-tertiary" : "text-fg-secondary",
                    )}
                  >
                    {reasonLabel(v.reason)}
                  </span>
                  <span className="shrink-0 text-xs text-fg-tertiary">{fmtDate(v.created_at)}</span>
                  <span className="min-w-0 flex-1 truncate text-xs text-fg-tertiary">
                    {/* 절대값(20절·20만자)은 어느 줄에서나 같아서 훑을 수가 없다 -
                        직전 버전 대비 변화를 앞세운다. 첫 버전만 절대값을 쓴다. */}
                    {v.n_changed_sections == null || v.delta_chars == null
                      ? `${v.n_sections}절 · ${v.total_chars.toLocaleString()}자`
                      : v.n_changed_sections === 0
                        ? "번호·제목만 바뀜"
                        : `${v.n_changed_sections}절 바뀜 · ${
                            v.delta_chars >= 0 ? "+" : ""
                          }${v.delta_chars.toLocaleString()}자`}
                    {v.created_by_name ? ` · ${v.created_by_name}` : ""}
                  </span>
                  {pickBase === null ? (
                    <>
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
                        className="h-7 px-1.5 text-xs text-fg-tertiary"
                        onClick={() => setPickBase(v.version_no)}
                        title="다른 버전과 견줍니다 - 이걸 한쪽으로 잡고 나머지 하나를 고르세요"
                        aria-label={`v${v.version_no}을 비교 한쪽으로 고르기`}
                      >
                        <Scale className="h-3.5 w-3.5" aria-hidden />
                      </Button>
                    </>
                  ) : v.version_no === pickBase ? (
                    <Badge variant="outline" className="shrink-0 border-accent text-accent">
                      고른 버전
                    </Badge>
                  ) : (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="h-7 px-2 text-xs"
                      onClick={() => {
                        // 오래된 쪽을 기준으로 - 문서가 흘러온 방향대로 읽힌다.
                        const [base, target] = [
                          Math.min(pickBase, v.version_no),
                          Math.max(pickBase, v.version_no),
                        ];
                        setPickBase(null);
                        onCompare(base, target);
                      }}
                    >
                      <GitCompare className="mr-1 h-3.5 w-3.5" aria-hidden />v{pickBase}과 비교
                    </Button>
                  )}
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
              );
            })}
          </ul>
        </>
      ) : null}
      {saving ? <SaveVersionDialog projectId={projectId} onClose={() => setSaving(false)} /> : null}
    </section>
  );
}
