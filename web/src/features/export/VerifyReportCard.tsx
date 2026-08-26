import {
  AlertTriangle,
  ArrowRight,
  ChevronDown,
  Loader2,
  RotateCw,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";
import { useState } from "react";
import { useProject } from "@/api/projects";
import {
  useRerunVerify,
  useResolveFinding,
  useVerifyCoverage,
  useVerifyReport,
  useVerifyStatus,
  type VerifyFinding,
} from "@/api/verify";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// PM 검증 경고 리포트 - 정적 게이트가 못 잡는 문서 횡단 문제(절 간 수치 충돌,
// 용어 표기 혼용, 법령 시점 상충)를 납품 전에 사람이 확인하는 카드.
// 차단이 아니라 참고용이며, 조회 실패(구백엔드 등) 시에는 조용히 숨긴다.
// 전체 목록은 수정할 수 있는 곳(미리보기·편집)에 두고, 출력 페이지는 compact
// 요약 배너만 쓴다(QA 산출물이 다운로드 화면을 지배하지 않게 - 2026-08-04).

// 검사가 못 보는 축 - 경고 0건이 '깨끗함'으로 읽히지 않게 항상 함께 보인다.
// (2026-08-14 실측: 진짜 결함 4건이 전부 이 축들에 있는데 critical 0이 떴다.)
const UNCOVERED_AXES = "절 간 지표 값 대조(예정)·자료 시점·코퍼스 밖 사실 검증";

// 활성 검사 축 요약 - 축별 상세는 경고 카테고리가 이미 말해준다.
const ACTIVE_AXES =
  "근거 대조(수치)·코퍼스 재검색(오귀속/창작 구분)·산술·형식·잔재·마커 오귀속·절 간 중복";

function CoverageLine({ projectId }: { projectId: string }) {
  const query = useVerifyCoverage(projectId);
  if (query.isLoading || query.isError || !query.data) return null;
  const c = query.data;
  const pct = c.claim_coverage === null ? null : Math.round(c.claim_coverage * 100);
  return (
    <div className="flex flex-col gap-0.5 text-xs text-fg-tertiary">
      <p>
        검사 범위 - 문장 {c.n_claims.toLocaleString()}/{c.n_candidates.toLocaleString()}
        {pct === null ? "" : ` (${pct}%)`} · {ACTIVE_AXES}
        {c.pm_verify_enabled ? " · 챕터 횡단(LLM)" : ""}
      </p>
      <p>못 보는 것 - {UNCOVERED_AXES}. 경고 없음이 무결을 뜻하지 않습니다</p>
      {c.missed_numeric > 0 ? (
        <p className="text-fg-danger">
          검사망 밖 수치 문장 {c.missed_numeric}건 - 문장 분해 회귀 의심(관리자 확인 필요)
        </p>
      ) : null}
      {!c.llm_verify_enabled ? (
        <p className="text-fg-warning">근거 동봉 판정 꺼짐 - 무근거 수치는 warning까지만 뜹니다</p>
      ) : null}
    </div>
  );
}
export function VerifyReportCard({
  projectId,
  compact = false,
  collapsible = false,
  onOpenEditor,
  onJump,
}: {
  projectId: string;
  compact?: boolean;
  /** 접힌 한 줄로 시작 - 편집 화면처럼 경고가 본작업을 가리면 안 되는 곳용 */
  collapsible?: boolean;
  onOpenEditor?: () => void;
  /** 경고 → 해당 절로 이동. 넘기면 목록 항목이 클릭 가능해진다.
   * section_id(절 안정 id)가 정본이고 section_ref는 표시값 폴백이다. */
  onJump?: (target: { section_ref?: string | null; section_id?: string | null }) => void;
}) {
  const query = useVerifyReport(projectId);
  const projectQuery = useProject(projectId);
  const rerun = useRerunVerify(projectId);
  const resolve = useResolveFinding(projectId);
  const status = useVerifyStatus(projectId).data;
  const running = status?.running ?? false;
  // 검증은 조립 때와 사람이 누를 때만 돈다 - 그 사이 절을 고치면 남은 경고(그리고
  // '경고 없음'까지)가 옛 본문에 대한 판정이 된다. 그 사실이 화면 어디에도 없었다
  // (2026-08-27). 서버가 판정 시점 본문 지문과 지금을 대조해 알려준다.
  const stale = (status?.stale ?? false) && !running;
  const [open, setOpen] = useState(false);
  // 처리한 경고는 기본으로 접는다 - 남은 일만 보이는 게 목록의 목적이다.
  const [showDone, setShowDone] = useState(false);

  if (query.isLoading || query.isError) return null;
  const all = query.data ?? [];
  const done = all.filter((f) => f.resolved);
  const findings = showDone ? all : all.filter((f) => !f.resolved);

  const verifiedAt = status?.verified_at ? new Date(status.verified_at) : null;
  const verifiedLabel =
    verifiedAt && !Number.isNaN(verifiedAt.getTime())
      ? `${verifiedAt.getMonth() + 1}/${verifiedAt.getDate()} ${String(
          verifiedAt.getHours(),
        ).padStart(2, "0")}:${String(verifiedAt.getMinutes()).padStart(2, "0")}`
      : null;
  // 낡음 표시 - 경고가 있든 없든 함께 붙는다. 0건이 낡은 채로 '깨끗함'처럼 읽히는
  // 것이 이 구멍에서 제일 위험하다.
  const staleBadge = stale ? (
    <span
      title={`마지막 검증${verifiedLabel ? ` ${verifiedLabel}` : ""} 이후 본문이 바뀌었습니다 - 지금 보이는 결과는 그때 본문에 대한 판정입니다`}
      className="inline-flex shrink-0 items-center gap-1 rounded border border-fg-warning/50 bg-bg-warning px-1.5 py-0.5 text-[11px] text-fg"
    >
      <AlertTriangle className="h-3 w-3" aria-hidden />
      검증 뒤 본문이 바뀜
    </span>
  ) : null;

  const rerunButton = (
    <Button
      size="sm"
      variant={stale ? "default" : "outline"}
      onClick={() => rerun.mutate()}
      disabled={running || rerun.isPending}
      title={
        stale
          ? "본문이 바뀌었습니다 - 지금 본문으로 다시 판정합니다"
          : "수정한 내용을 반영해 검증을 다시 실행합니다"
      }
    >
      {running || rerun.isPending ? (
        <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden />
      ) : (
        <RotateCw className="mr-1 h-3.5 w-3.5" aria-hidden />
      )}
      {running || rerun.isPending ? "검증 중…" : "다시 검증"}
    </Button>
  );

  if (all.length === 0) {
    // 빈 결과 ≠ 통과: PM 검증은 조립 단계(완료 직전)에 돌므로, 완료 전에는
    // "아직 검증 전"이다 - 통과로 표시하면 거짓 안심을 준다(2026-08-05 지적).
    const status = projectQuery.data?.status;
    if (status !== "completed" && status !== "archived") {
      return (
        <div className="flex items-center gap-2 rounded border border-border bg-bg p-3 text-sm text-fg-secondary">
          <Loader2 className="h-4 w-4 shrink-0 animate-spin text-fg-tertiary" aria-hidden />
          PM 검증 대기 중 - 보고서 조립·검증이 끝나면 결과가 여기에 표시됩니다
        </div>
      );
    }
    return (
      // '통과'라고 쓰지 않는다 - 경고 0은 "검사한 축에서 못 찾음"이지 무결이 아니다.
      // 검사 범위와 못 보는 축을 같이 보여야 거짓 안심을 안 준다(2026-08-14).
      <div className="flex flex-col gap-1.5 rounded border border-border bg-bg p-3 text-sm text-fg-secondary">
        <div className="flex flex-wrap items-center gap-2">
          <ShieldCheck className="h-4 w-4 shrink-0 text-fg-success" aria-hidden />
          PM 검증 경고 없음 - 아래 검사 범위에서 지적할 것을 찾지 못했습니다
          {staleBadge}
          <span className="ml-auto">{rerunButton}</span>
        </div>
        <CoverageLine projectId={projectId} />
      </div>
    );
  }

  const criticalCount = findings.filter((f) => f.severity === "critical").length;

  if (collapsible && !open) {
    // 접힌 한 줄 - 편집 화면에서 경고가 본작업을 가리지 않게 하고, 필요할 때만 편다.
    // 재검증은 펼치지 않고도 눌러야 한다: 고치고 나서 하는 일이 "다시 확인"이라
    // 접힌 상태가 그 시점의 기본 화면이다(버튼 중첩은 invalid HTML이라 형제로 둔다).
    return (
      <div
        className={cn(
          "flex flex-wrap items-center gap-2 rounded border p-3 text-sm",
          criticalCount > 0
            ? "border-fg-danger/40 bg-bg-danger"
            : "border-fg-warning/40 bg-bg-warning",
        )}
      >
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="flex min-w-0 flex-1 flex-wrap items-center gap-2 text-left"
        >
          {criticalCount > 0 ? (
            <ShieldAlert className="h-4 w-4 shrink-0 text-fg-danger" aria-hidden />
          ) : (
            <AlertTriangle className="h-4 w-4 shrink-0 text-fg-warning" aria-hidden />
          )}
          <span className="font-medium text-fg">PM 검증 경고 {findings.length}건</span>
          {criticalCount > 0 ? (
            // 총 건수만으론 심각한 게 섞였는지 알 수 없다 - critical은 별도 배지로 분리
            <Badge variant="destructive" className="shrink-0">
              critical {criticalCount}
            </Badge>
          ) : null}
          {done.length > 0 ? (
            <span className="text-xs text-fg-tertiary">처리함 {done.length}건</span>
          ) : null}
          <span className="text-xs text-fg-tertiary">펼쳐서 항목을 누르면 해당 절로 이동</span>
          <ChevronDown className="h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
        </button>
        {staleBadge}
        {rerunButton}
      </div>
    );
  }

  if (compact) {
    return (
      <div
        className={cn(
          "flex flex-wrap items-center gap-2 rounded border p-3 text-sm",
          criticalCount > 0
            ? "border-fg-danger/40 bg-bg-danger"
            : "border-fg-warning/40 bg-bg-warning",
        )}
      >
        {criticalCount > 0 ? (
          <ShieldAlert className="h-4 w-4 shrink-0 text-fg-danger" aria-hidden />
        ) : (
          <AlertTriangle className="h-4 w-4 shrink-0 text-fg-warning" aria-hidden />
        )}
        <span className="font-medium text-fg">
          PM 검증 경고 {findings.length}건{criticalCount > 0 ? ` (critical ${criticalCount})` : ""}
        </span>
        <span className="text-xs text-fg-tertiary">납품 전 확인 권장</span>
        {staleBadge}
        {onOpenEditor ? (
          <Button size="sm" variant="outline" className="ml-auto" onClick={onOpenEditor}>
            미리보기·편집에서 확인 <ArrowRight className="ml-1 h-3.5 w-3.5" />
          </Button>
        ) : null}
      </div>
    );
  }
  const byChapter = new Map<number, VerifyFinding[]>();
  for (const f of findings) {
    const arr = byChapter.get(f.chapter_number) ?? [];
    arr.push(f);
    byChapter.set(f.chapter_number, arr);
  }

  return (
    <section
      aria-label="PM 검증 경고 리포트"
      className={cn(
        "flex flex-col gap-3 rounded border p-4",
        criticalCount > 0
          ? "border-fg-danger/40 bg-bg-danger"
          : "border-fg-warning/40 bg-bg-warning",
      )}
    >
      <header className="flex items-center gap-2">
        {criticalCount > 0 ? (
          <ShieldAlert className="h-4 w-4 shrink-0 text-fg-danger" aria-hidden />
        ) : (
          <AlertTriangle className="h-4 w-4 shrink-0 text-fg-warning" aria-hidden />
        )}
        <h2 className="text-sm font-semibold text-fg">
          PM 검증 경고 {findings.length}건{criticalCount > 0 ? ` (critical ${criticalCount})` : ""}
        </h2>
        <span className="text-xs text-fg-tertiary">납품 전 확인 권장 - 편집기에서 수정 가능</span>
        <div className="ml-auto flex items-center gap-2">
          {staleBadge}
          {verifiedLabel && !stale ? (
            <span className="text-xs text-fg-tertiary">마지막 검증 {verifiedLabel}</span>
          ) : null}
          {done.length > 0 ? (
            <button
              type="button"
              onClick={() => setShowDone((v) => !v)}
              className="text-xs text-fg-info hover:underline"
            >
              {showDone ? "남은 것만 보기" : `처리함 ${done.length}건 보기`}
            </button>
          ) : null}
          {rerunButton}
        </div>
        {collapsible ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setOpen(false)}
            aria-label="경고 목록 접기"
          >
            접기 <ChevronDown className="ml-1 h-3.5 w-3.5 rotate-180" aria-hidden />
          </Button>
        ) : null}
      </header>

      <CoverageLine projectId={projectId} />

      <div className="flex flex-col gap-3">
        {[...byChapter.entries()].map(([chapter, items]) => (
          <div key={chapter} className="flex flex-col gap-1.5">
            <p className="text-xs font-medium text-fg-secondary">{chapter}장</p>
            <ul className="flex flex-col gap-1.5">
              {items.map((f) => (
                <li
                  key={f.id}
                  className={cn(
                    "flex items-start gap-2 rounded border border-border bg-bg px-3 py-2",
                    f.resolved && "opacity-60",
                  )}
                >
                  <input
                    type="checkbox"
                    checked={f.resolved}
                    onChange={(e) => resolve.mutate({ id: f.id, resolved: e.target.checked })}
                    className="mt-0.5 h-4 w-4 shrink-0 cursor-pointer accent-accent"
                    aria-label={`${f.category} 처리함으로 표시`}
                  />
                  <button
                    type="button"
                    disabled={!onJump || (!f.section_ref && !f.section_id)}
                    onClick={() => (f.section_ref || f.section_id) && onJump?.(f)}
                    className={cn(
                      "flex min-w-0 flex-1 items-start gap-2 text-left text-sm",
                      onJump && (f.section_ref || f.section_id)
                        ? "cursor-pointer hover:underline"
                        : "cursor-default",
                      f.resolved && "line-through",
                    )}
                  >
                    <Badge
                      variant={f.severity === "critical" ? "destructive" : "outline"}
                      className={cn(
                        "shrink-0 font-normal",
                        f.severity !== "critical" && "border-fg-warning/40 bg-bg-warning",
                      )}
                    >
                      {f.category}
                    </Badge>
                    <span className="min-w-0 text-fg">
                      {f.section_ref ? (
                        <span className="mr-1 font-mono text-xs text-fg-tertiary">
                          §{f.section_ref}
                        </span>
                      ) : null}
                      {f.detail}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
