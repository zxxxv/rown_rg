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
  useVerifyReport,
  useVerifyStatus,
  type VerifyFinding,
} from "@/api/verify";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

// PM 검증 경고 리포트 - 정적 게이트가 못 잡는 문서 횡단 문제(절 간 수치·용어 충돌,
// 법령 시점 상충, 챕터 간 통계 중복)를 납품 전에 사람이 확인하는 카드.
// 차단이 아니라 참고용이며, 조회 실패(구백엔드 등) 시에는 조용히 숨긴다.
// 전체 목록은 수정할 수 있는 곳(미리보기·편집)에 두고, 출력 페이지는 compact
// 요약 배너만 쓴다(QA 산출물이 다운로드 화면을 지배하지 않게 - 2026-08-04).
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
  /** 경고 → 해당 절로 이동. 넘기면 목록 항목이 클릭 가능해진다(§1.1 같은 ref 기준). */
  onJump?: (sectionRef: string) => void;
}) {
  const query = useVerifyReport(projectId);
  const projectQuery = useProject(projectId);
  const rerun = useRerunVerify(projectId);
  const resolve = useResolveFinding(projectId);
  const running = useVerifyStatus(projectId).data?.running ?? false;
  const [open, setOpen] = useState(false);
  // 처리한 경고는 기본으로 접는다 - 남은 일만 보이는 게 목록의 목적이다.
  const [showDone, setShowDone] = useState(false);

  if (query.isLoading || query.isError) return null;
  const all = query.data ?? [];
  const done = all.filter((f) => f.resolved);
  const findings = showDone ? all : all.filter((f) => !f.resolved);

  const rerunButton = (
    <Button
      size="sm"
      variant="outline"
      onClick={() => rerun.mutate()}
      disabled={running || rerun.isPending}
      title="수정한 내용을 반영해 검증을 다시 실행합니다"
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
      <div className="flex flex-wrap items-center gap-2 rounded border border-border bg-bg p-3 text-sm text-fg-secondary">
        <ShieldCheck className="h-4 w-4 shrink-0 text-fg-success" aria-hidden />
        PM 검증 경고 없음 - 절 간 수치·용어·법령 시점 교차검사 통과
        <span className="ml-auto">{rerunButton}</span>
      </div>
    );
  }

  const criticalCount = findings.filter((f) => f.severity === "critical").length;

  if (collapsible && !open) {
    // 접힌 한 줄 - 편집 화면에서 경고가 본작업을 가리지 않게 하고, 필요할 때만 편다.
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className={cn(
          "flex w-full flex-wrap items-center gap-2 rounded border p-3 text-left text-sm transition-colors",
          criticalCount > 0
            ? "border-fg-danger/40 bg-bg-danger hover:border-fg-danger"
            : "border-fg-warning/40 bg-bg-warning hover:border-fg-warning",
        )}
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
        <span className="text-xs text-fg-tertiary">펼쳐서 항목을 누르면 해당 절로 이동합니다</span>
        <ChevronDown className="ml-auto h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
      </button>
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
                    disabled={!onJump || !f.section_ref}
                    onClick={() => f.section_ref && onJump?.(f.section_ref)}
                    className={cn(
                      "flex min-w-0 flex-1 items-start gap-2 text-left text-sm",
                      onJump && f.section_ref ? "cursor-pointer hover:underline" : "cursor-default",
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
