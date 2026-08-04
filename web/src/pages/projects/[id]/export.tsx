import { ArrowLeft, ArrowRight, CheckCircle2, Download } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { useProject } from "@/api/projects";
import type { Project } from "@/api/types";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { env } from "@/env";
import { useDownload } from "@/features/export/useDownload";
import { VerifyReportCard } from "@/features/export/VerifyReportCard";
import { useAuth } from "@/hooks/useAuth";

export default function ExportPage() {
  const { id: projectId = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { user, logout } = useAuth();

  const projectQuery = useProject(projectId);
  const project = projectQuery.data;

  return (
    <AppShell
      user={user ? { name: user.name, role: user.role } : null}
      onLogout={() => void logout()}
      tokenUsage={{ used: 1_240_000, limit: 5_000_000 }}
    >
      <div className="flex flex-col gap-6">
        <Button
          variant="ghost"
          size="sm"
          className="w-fit text-fg-secondary"
          onClick={() => navigate(`/projects/${projectId}/overview`)}
        >
          <ArrowLeft className="mr-1 h-4 w-4" />
          프로젝트 개요
        </Button>

        {projectQuery.isLoading ? (
          <LoadingSkeleton variant="block" />
        ) : projectQuery.isError || !project ? (
          <EmptyState
            title="프로젝트를 찾을 수 없습니다"
            action={
              <Button variant="outline" onClick={() => navigate("/projects")}>
                목록으로
              </Button>
            }
          />
        ) : project.status !== "completed" ? (
          <NotReadyView project={project} onNavigate={navigate} />
        ) : (
          <CompletedView project={project} />
        )}
      </div>
    </AppShell>
  );
}

function NotReadyView({
  project,
  onNavigate,
}: {
  project: Project;
  onNavigate: (to: string) => void;
}) {
  return (
    <EmptyState
      title="아직 완료되지 않은 보고서입니다"
      description={`현재 상태: ${project.status} — 작성·검증이 끝난 후 출력할 수 있습니다.`}
      action={
        <Button onClick={() => onNavigate(`/projects/${project.id}/overview`)}>
          개요의 진행 단계로 <ArrowRight className="ml-1 h-4 w-4" />
        </Button>
      }
    />
  );
}

function CompletedView({ project }: { project: Project }) {
  const navigate = useNavigate();
  const download = useDownload();
  const completedAt = project.updated_at?.slice(0, 10) ?? project.created_at.slice(0, 10);

  // 카드(미리보기 자리표시자+양식 표)는 제거 — 우상단 다운로드 버튼 하나로 단순화
  // (2026-08-04 사용자 요청). 실동작은 HWPX 다운로드 + 검증 리포트 두 가지뿐이다.
  return (
    <>
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <h1 className="text-3xl font-semibold text-fg">보고서 출력</h1>
            <Badge variant="default" className="bg-bg-success text-fg-success">
              <CheckCircle2 className="mr-1 h-3.5 w-3.5" />
              작성 완료
            </Badge>
          </div>
          <p className="text-sm text-fg-secondary">{project.title}</p>
          <dl className="flex flex-wrap gap-x-4 font-mono text-xs text-fg-tertiary">
            <Meta label="작성 완료" value={completedAt} />
            <Meta label="양식" value="회사 표준 (함초롬바탕 11pt)" />
          </dl>
        </div>
        <Button
          size="lg"
          onClick={() =>
            download({
              // 파일 응답 — ky 언래핑 없이 브라우저 다운로드로 직접 연결한다.
              url: `${env.VITE_API_BASE_URL.replace(/\/$/, "")}/projects/${project.id}/export`,
              filename: `${project.title}.hwpx`,
              label: "HWPX",
            })
          }
        >
          <Download className="mr-1 h-4 w-4" />
          HWPX 다운로드
        </Button>
      </header>

      {/* QA 산출물의 전체 목록은 수정 가능한 미리보기·편집에 있다 — 여기선 요약만 */}
      <VerifyReportCard
        projectId={project.id}
        compact
        onOpenEditor={() => navigate(`/projects/${project.id}/preview`)}
      />
    </>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-1">
      <dt>{label}</dt>
      <dd className="text-fg-secondary">{value}</dd>
    </div>
  );
}
