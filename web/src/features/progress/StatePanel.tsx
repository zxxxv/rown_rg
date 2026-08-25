import {
  AlertTriangle,
  ArrowRight,
  Download,
  FileText,
  FolderOpen,
  History,
  ListTree,
  Loader2,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import type { ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useDrift } from "@/api/drift";
import { useInsights } from "@/api/insights";
import type { ProgressSnapshot } from "@/api/progress";
import { useProjectSections } from "@/api/sections";
import { useProjectSources } from "@/api/sources";
import type { Project } from "@/api/types";
import { useRerunVerify, useVerifyReport } from "@/api/verify";
import { useReportVersions } from "@/api/versions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { env } from "@/env";
import { useDownload } from "@/features/export/useDownload";
import { ElapsedRow } from "@/features/progress/PipelineStepper";
import { reasonLabel } from "@/features/versions/VersionHistoryCard";

/** 상태 패널 - 런이 멈춰 있을 때의 우측 기둥.
 *
 * 진행 스테퍼("어디까지 왔나")를 대신한다. 완주하고 나면 스테퍼는 전부 체크된 채
 * 굳어 아무 정보도 못 주는데, 정작 그때부터가 품질을 보고 계속 손보는 구간이다
 * (2026-08-25 설계 전환).
 *
 * 각 줄은 '지나온 단계'가 아니라 **내가 소유한 산출물**이고, 버튼은 그 물건을 여는
 * 문이다. 되감기가 아니라서 어느 줄이든 눌러도 뒤 단계가 무효가 되지 않는다 -
 * 고치면 영향받은 절만 '미반영'으로 드러난다. 그래서 버튼이 상태와 무관하게 늘
 * 살아 있고, 그 사실 자체가 "어디로든 갈 수 있다"를 알린다.
 */
export function StatePanel({
  project,
  snapshot,
  onOpenConfig,
}: {
  project: Project;
  /** 진행 스냅샷 - 생성 시간을 그린다. 스테퍼 안에만 있어서 완주 뒤 사라졌었다. */
  snapshot: ProgressSnapshot | undefined;
  /** 목차 편집기를 연다 - 편집기는 개요의 '프로젝트 옵션' 아코디언 안에 있어
   *  라우팅이 아니라 부모의 펼침 상태를 건드려야 한다. */
  onOpenConfig: () => void;
}) {
  const projectId = project.id;
  const navigate = useNavigate();
  const [, setParams] = useSearchParams();

  const sources = useProjectSources(projectId);
  const sections = useProjectSections(projectId);
  const drift = useDrift(projectId);
  const verify = useVerifyReport(projectId);
  const versions = useReportVersions(projectId);
  const insights = useInsights(projectId);
  const rerunVerify = useRerunVerify(projectId);
  const { download, pending: downloading } = useDownload();

  const items = sources.data?.items ?? [];
  const nAdopted = items.filter((s) => s.is_included !== false).length;
  const nIndexing = items.filter((s) => s.indexing).length;

  const tree = sections.data?.tree ?? [];
  const leaves = tree.flatMap((c) => c.children);
  const nDone = leaves.filter((s) => s.status === "completed").length;
  const nUnreflected = drift.data?.sections.length ?? 0;

  const findings = verify.data ?? [];
  const nCritical = findings.filter((f) => f.severity === "critical").length;

  const nVersions = versions.data?.length ?? 0;
  // 목록은 최신순 - 마지막에 무슨 일이 있었는지가 한눈에 들어와야 한다.
  const latest = versions.data?.[0];
  const hasInsights = Boolean(insights.data?.content);

  return (
    <section className="flex flex-col rounded-lg border border-border bg-bg">
      <header className="border-b border-border px-4 py-3">
        <h2 className="text-sm font-semibold text-fg">상태</h2>
      </header>

      <Row
        icon={<ListTree className="h-4 w-4" aria-hidden />}
        label="설계"
        detail={`${leaves.length || countOutlineSections(project)}절 · ${project.updated_at.slice(5, 10)} 수정`}
        action={
          <Button variant="outline" size="sm" onClick={onOpenConfig}>
            목차 편집
            <ArrowRight className="ml-1 h-3.5 w-3.5" />
          </Button>
        }
      />

      <Row
        icon={<FolderOpen className="h-4 w-4" aria-hidden />}
        label="자료"
        detail={
          sources.isLoading
            ? "불러오는 중"
            : items.length === 0
              ? "아직 없음"
              : `채택 ${nAdopted}건${nIndexing > 0 ? ` · 색인 중 ${nIndexing}` : " · 색인 완료"}`
        }
        action={
          <Button
            variant="outline"
            size="sm"
            onClick={() => navigate(`/projects/${projectId}/sources`)}
          >
            자료 열기
            <ArrowRight className="ml-1 h-3.5 w-3.5" />
          </Button>
        }
      />

      <Row
        icon={<FileText className="h-4 w-4" aria-hidden />}
        label="본문"
        detail={`${nDone}/${leaves.length} 완성`}
        badge={
          nUnreflected > 0 ? (
            <Badge className="border-fg-warning/30 bg-bg-warning font-normal text-fg-warning">
              <AlertTriangle className="mr-1 h-3 w-3" aria-hidden />
              미반영 {nUnreflected}
            </Badge>
          ) : null
        }
        action={
          <Button
            variant="outline"
            size="sm"
            onClick={() => navigate(`/projects/${projectId}/preview`)}
          >
            본문 열기
            <ArrowRight className="ml-1 h-3.5 w-3.5" />
          </Button>
        }
      />

      <Row
        icon={<ShieldCheck className="h-4 w-4" aria-hidden />}
        label="검증"
        detail={findings.length === 0 ? "경고 없음" : `경고 ${findings.length}건`}
        badge={
          nCritical > 0 ? (
            <Badge className="border-fg-danger/30 bg-bg-danger font-normal text-fg-danger">
              critical {nCritical}
            </Badge>
          ) : null
        }
        action={
          <Button
            variant="outline"
            size="sm"
            disabled={rerunVerify.isPending}
            onClick={() => rerunVerify.mutate()}
          >
            {rerunVerify.isPending ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="mr-1 h-3.5 w-3.5" />
            )}
            다시 검증
          </Button>
        }
      />

      <Row
        icon={<Download className="h-4 w-4" aria-hidden />}
        label="산출물"
        detail={`HWPX${hasInsights ? " · 시사점 요약" : ""}`}
        action={
          <Button
            variant="outline"
            size="sm"
            disabled={downloading}
            onClick={() =>
              void download({
                url: `${env.VITE_API_BASE_URL.replace(/\/$/, "")}/projects/${projectId}/export`,
                filename: `${project.title}.hwpx`,
                label: "HWPX",
              })
            }
          >
            <Download className="mr-1 h-3.5 w-3.5" />
            {downloading ? "준비 중" : "다운로드"}
          </Button>
        }
      />

      <Row
        icon={<History className="h-4 w-4" aria-hidden />}
        label="버전"
        detail={
          latest
            ? `${nVersions}개 · 최신 v${latest.version_no} (${reasonLabel(latest.reason)})`
            : "아직 없음"
        }
        action={
          <Button
            variant="outline"
            size="sm"
            disabled={nVersions === 0}
            onClick={() => {
              // 목록·비교는 넓은 자리가 필요해 본문 열에 둔다 - 패널은 문만 연다
              // ('목차 편집'이 프로젝트 옵션을 펴는 것과 같은 방식).
              setParams(
                (prev) => {
                  const next = new URLSearchParams(prev);
                  next.set("versions", "1");
                  return next;
                },
                { replace: true },
              );
              document.getElementById("version-history")?.scrollIntoView({ behavior: "smooth" });
            }}
          >
            기록 열기
            <ArrowRight className="ml-1 h-3.5 w-3.5" />
          </Button>
        }
        last
      />

      {/* 생성 시간 - 스테퍼가 쓰던 것을 그대로 재사용한다. 검토 대기·중단 구간을 빼는
          규칙에 실사고가 걸려 있어(게이트 9시간이 섞여 22시간으로 보였다) 두 벌로
          만들지 않는다. */}
      {snapshot ? (
        <div className="px-4 pb-3">
          <ElapsedRow snapshot={snapshot} />
        </div>
      ) : null}
    </section>
  );
}

/** 절 행이 아직 없을 때(작성 전)의 폴백 - 목차 정본에서 절 수를 센다. */
function countOutlineSections(project: Project): number {
  const outline = (project.config as { outline?: { chapters?: { sections?: unknown[] }[] } })
    ?.outline;
  return (outline?.chapters ?? []).reduce((n, ch) => n + (ch.sections?.length ?? 0), 0);
}

function Row({
  icon,
  label,
  detail,
  badge,
  action,
  last,
}: {
  icon: ReactNode;
  label: string;
  detail: string;
  badge?: ReactNode;
  action: ReactNode;
  last?: boolean;
}) {
  return (
    <div
      className={`flex flex-wrap items-center justify-between gap-2 px-4 py-3 ${last ? "" : "border-b border-border"}`}
    >
      <div className="flex min-w-0 flex-col gap-0.5">
        <span className="flex items-center gap-1.5 text-sm font-medium text-fg">
          <span className="text-fg-secondary">{icon}</span>
          {label}
          {badge}
        </span>
        <span className="text-xs text-fg-tertiary">{detail}</span>
      </div>
      {action}
    </div>
  );
}
