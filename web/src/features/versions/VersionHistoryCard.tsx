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
import { apiUrl } from "@/api/client";
import { useReportVersions } from "@/api/versions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { HelpTip } from "@/components/ui/help-tip";
import { useDownload } from "@/features/export/useDownload";
import { fmtKstCompact } from "@/lib/datetime";
import { cn } from "@/lib/utils";
import { isMilestoneReason, reasonLabel } from "./reasons";
import { SaveVersionDialog } from "./SaveVersionDialog";

// 버전 기록 - 커밋 로그처럼 읽힌다: 언제·왜(완성/재개 보존/재작성/확정)·규모.
// 스냅샷은 서버가 자동으로 쌓고(조립·재개·재작성·편집·확정), 수동 저장 버튼이 그 사이를 메운다.
//
// 기본 목록은 **이정표만** 보여준다(reasons.isMilestoneReason). 절을 하나 고칠 때마다
// 자동으로 쌓이는 스냅샷은 되돌리기용 안전망이지 사람이 읽는 이력이 아니라, 다 늘어놓으면
// 35절 보고서 한 바퀴에 수십 줄이 되어 정작 찾는 지점이 묻힌다(2026-08-27).

// 표시는 KST 고정(fmtKstCompact) - 버전 이력만 브라우저 로컬 시간이라 화면마다
// 시각이 달랐다.

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
          {/* 수는 **전체**를 말한다. 한때 접힌 목록에 맞춰 "주요 버전 4개"라고 썼는데,
              '주요'가 무슨 뜻인지가 화면 어디에도 없어 새 낱말만 하나 는 셈이었다
              (2026-08-27 지적). 접었다는 사실은 바로 아래 토글 줄이 이미 말한다. */}
          <span className="text-xs text-fg-tertiary">
            {versions.length}개 · 마지막 변경 v{versions[0].version_no} (
            {fmtKstCompact(versions[0].created_at)})
          </span>
        </button>
        {/* 접는 머리가 <button>이라 도움말을 그 안에 넣으면 버튼 안 버튼이 된다
            - 형제로 둔다. */}
        <HelpTip title="버전 기록이란?" className="shrink-0">
          <p>
            본문이 바뀌는 순간마다 그때 원고를 통째로 얼려 둡니다. 나중에 "그때가 나았는데" 싶으면{" "}
            <b>절 하나만</b> 그 시점으로 되돌릴 수 있습니다(전체 롤백은 없습니다 - 그 사이 손본 다른
            절까지 잃으니까요).
          </p>
          <p className="mt-2">
            자동으로 쌓이는 때: 보고서 완성, 다시 열기 직전, 최종 확정, 목차·자료 변경, 그리고 절을
            재작성하거나 직접 고칠 때마다.
          </p>
          <p className="mt-2">
            <b>버전 저장</b>은 그 사이에 사람이 직접 찍는 표식입니다. 이름을 붙여 두면 나중에
            목록에서 찾을 지점이 됩니다. 같은 내용이면 새 버전을 만들지 않으니 눌러도 손해가
            없습니다.
          </p>
          <p className="mt-2 text-fg-tertiary">
            목록은 기본으로 문서 전체가 달라진 지점만 보여줍니다. 절 하나를 고칠 때마다 쌓이는
            기록은 아래 토글로 펼칩니다.
          </p>
          <p className="mt-2 text-fg-tertiary">
            {/* 한 벌이 보고서 전체라 잦은 수정을 다 쌓으면 프로젝트 하나가 수십 MB가 된다.
                지우는 규칙이 있다는 사실은 밝혀야 한다 - 없다고 믿고 옛것을 찾으면
                그때 알게 되는 건 늦다(2026-08-27). */}
            재작성·직접 수정 기록은 <b>최근 20개</b>까지 남습니다. 완성·확정·다시 열기·목차 변경
            같은 마디와 직접 찍은 <b>버전 저장</b>은 개수와 상관없이 계속 남습니다.
          </p>
        </HelpTip>
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
                  <span className="shrink-0 text-xs text-fg-tertiary">
                    {fmtKstCompact(v.created_at)}
                  </span>
                  {v.is_current ? (
                    <Badge
                      variant="outline"
                      className="shrink-0 border-fg-success/40 bg-bg-success text-fg-success"
                    >
                      지금 본문
                    </Badge>
                  ) : null}
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
                        url: apiUrl(`projects/${projectId}/versions/${v.version_no}/download`),
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
