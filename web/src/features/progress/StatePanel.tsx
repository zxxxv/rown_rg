import {
  AlertTriangle,
  ArrowRight,
  Download,
  FileText,
  FolderOpen,
  History,
  ListTree,
  ShieldCheck,
} from "lucide-react";
import { type ReactNode, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useDrift } from "@/api/drift";
import { useInsights } from "@/api/insights";
import type { ProgressSnapshot } from "@/api/progress";
import { useProjectSections } from "@/api/sections";
import { useProjectSources } from "@/api/sources";
import type { Project } from "@/api/types";
import { useVerifyReport } from "@/api/verify";
import { useReportVersions } from "@/api/versions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { env } from "@/env";
import { useDownload } from "@/features/export/useDownload";
import { ElapsedRow } from "@/features/progress/PipelineStepper";
import { reasonLabel } from "@/features/versions/reasons";
import { VersionHistoryCard } from "@/features/versions/VersionHistoryCard";

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

  const sources = useProjectSources(projectId);
  const sections = useProjectSections(projectId);
  const drift = useDrift(projectId);
  const verify = useVerifyReport(projectId);
  const versions = useReportVersions(projectId);
  const insights = useInsights(projectId);
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
  const [versionsOpen, setVersionsOpen] = useState(false);

  return (
    <section className="flex flex-col rounded-lg border border-border bg-bg">
      {/* 패널 제목은 없앴다 - 아래 두 묶음 머리(보고서 만들기 / 확인하고 내보내기)가
          이미 무엇을 모아 둔 자리인지 말한다. 그 위에 "상태"든 "이 보고서"든 한 겹
          더 얹으면 같은 말을 두 번 하는 셈이었다(2026-08-27 지적). */}
      {/* 두 묶음으로 가른 기준은 **문서를 바꾸는가**다. 설계·자료·본문을 건드리면 문서
          자체가 달라지고, 검증·산출물·버전은 만들어진 것을 확인하고 꺼내는 자리다.
          여섯 줄이 평평하게 늘어서 있으면 "어디까지가 만드는 일인가"가 안 읽힌다
          (2026-08-27 지적). */}
      <GroupHead>보고서 만들기</GroupHead>
      <Row
        icon={<ListTree className="h-4 w-4" aria-hidden />}
        label="설계"
        detail={
          <span className="flex flex-wrap items-center gap-x-1.5">
            <span>
              {leaves.length || countOutlineSections(project)}절 · {project.updated_at.slice(5, 10)}{" "}
              수정
            </span>
            <button
              type="button"
              className="text-fg-info underline-offset-2 hover:underline"
              onClick={() => navigate(`/projects/${projectId}/brief`)}
            >
              설계 검토 보기
            </button>
          </span>
        }
        action={
          // 오른쪽 버튼은 줄마다 **하나**다 - 둘을 나란히 두니 줄바꿈이 나면서 이 줄만
          // 왼쪽으로 흘러 다른 줄과 어긋났다(2026-08-27 지적). 읽기 전용인 '설계 검토'는
          // 아래 설명 줄의 링크로 내린다 - 고치는 문(목차 편집)만 버튼으로 남긴다.
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
          // 본문은 **이 화면 아래에 이미 있다**. 예전에도 같은 이유로 '보고서
          // 미리보기' 카드를 걷어냈는데(2026-08-09), 패널을 만들며 되살아났다
          // - 버튼이 자기 아래 내용을 가리키면 누른 사람은 화면이 바뀔 줄 안다.
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              document.getElementById("report-body")?.scrollIntoView({ behavior: "smooth" })
            }
          >
            본문 보기
            <ArrowRight className="ml-1 h-3.5 w-3.5" />
          </Button>
        }
      />

      <GroupHead>확인하고 내보내기</GroupHead>
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
          // '다시 검증'은 경고를 보고 있는 카드에만 둔다 - 같은 일을 하는 버튼이 화면에
          // 둘이면 어느 쪽을 눌러야 하는지 묻게 된다(2026-08-27 지적, 설계·자료 카드를
          // 걷어낸 것과 같은 이유). 패널은 그 자리로 데려가는 문만 맡는다.
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              document.getElementById("pm-verify")?.scrollIntoView({ behavior: "smooth" })
            }
          >
            경고 보기
            <ArrowRight className="ml-1 h-3.5 w-3.5" />
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
            ? `${nVersions}개 · 마지막 v${latest.version_no} (${reasonLabel(latest.reason)})`
            : "아직 없음"
        }
        action={
          // 목록은 **시트**로 연다 - 본문 열에 끼워 두면 PM 경고와 본문 사이를 가로막아
          // 읽는 흐름이 끊긴다(2026-08-27 지적). 비교(diff)는 넓은 자리가 필요하니
          // 지금처럼 본문 자리를 쓰고, 그때는 시트가 닫힌다.
          <Button
            variant="outline"
            size="sm"
            disabled={nVersions === 0}
            onClick={() => setVersionsOpen(true)}
          >
            기록 열기
            <ArrowRight className="ml-1 h-3.5 w-3.5" />
          </Button>
        }
        last
      />

      <Sheet open={versionsOpen} onOpenChange={setVersionsOpen}>
        <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-xl">
          <SheetHeader>
            <SheetTitle>버전 기록</SheetTitle>
          </SheetHeader>
          <div className="mt-4">
            <VersionHistoryCard
              projectId={projectId}
              projectTitle={project.title}
              alwaysOpen
              onCompare={(v, target) => {
                // 비교는 본문 자리를 쓴다 - 시트를 닫고 **미리보기로 넘긴다**.
                // 여기(개요)에 파라미터만 얹으면 아무 일도 안 일어난다: 비교 뷰는
                // 미리보기에만 있어서 버튼이 죽은 문이 된다(2026-08-27 실측).
                // target이 있으면 버전끼리 견주는 것(없으면 현재 본문과).
                setVersionsOpen(false);
                const q = new URLSearchParams({ compare: String(v) });
                if (target !== undefined) q.set("compareTo", String(target));
                navigate(`/projects/${projectId}/preview?${q.toString()}`);
              }}
            />
          </div>
        </SheetContent>
      </Sheet>

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

/** 묶음 머리 - 줄 여섯 개를 성격으로 가른다(바꾸는 자리 / 확인하는 자리). */
function GroupHead({ children }: { children: React.ReactNode }) {
  return (
    <p className="border-b border-border bg-bg-secondary/50 px-4 py-1.5 text-[11px] font-medium text-fg-tertiary">
      {children}
    </p>
  );
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
  detail: ReactNode;
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
