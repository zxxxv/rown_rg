import {
  ArrowLeft,
  ArrowRight,
  Download,
  Eye,
  FileSearch,
  GitCompare,
  PlayCircle,
  ScanLine,
  Settings2,
  SquarePen,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { useProjectContradictions } from "@/api/contradictions";
import { useProgressSnapshot } from "@/api/progress";
import { useDeleteProject, useProject, useUpdateProjectConfig } from "@/api/projects";
import type { Project, ProjectStatus } from "@/api/types";
import { StatusDot, type StatusKind } from "@/components/data-display/StatusDot";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ProjectConfigForm } from "@/features/project-config/ProjectConfigForm";
import { presetLabel } from "@/features/project-config/presets";
import type { ProjectFormValues } from "@/features/project-config/schema";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";

const STATUS_LABEL: Record<ProjectStatus, string> = {
  created: "생성됨",
  researching: "자료 수집",
  indexing: "인덱싱",
  writing: "작성 중",
  reviewing: "검토 대기",
  completed: "완료",
  archived: "보관",
};

const STATUS_KIND: Record<ProjectStatus, StatusKind> = {
  created: "tertiary",
  researching: "info",
  indexing: "info",
  writing: "info",
  reviewing: "warning",
  completed: "success",
  archived: "tertiary",
};

// 단계 기반 근사 진행률 — 백엔드 _STAGE_PERCENT와 동일. ProjectRead에 progress가 없어
// (실백엔드) 상태에서 유도한다. 완료·보관은 100%.
const STATUS_PERCENT: Record<ProjectStatus, number> = {
  created: 0,
  researching: 20,
  indexing: 40,
  writing: 60,
  reviewing: 85,
  completed: 100,
  archived: 100,
};

const NEXT_STEP_HINT: Record<ProjectStatus, string> = {
  created: "옵션 검토 후 작성을 시작하세요.",
  researching: "AI가 자료를 수집·평가하고 있습니다.",
  indexing: "수집한 자료를 청크·임베딩으로 변환 중입니다.",
  writing: "Level 1~4 본문을 작성 중입니다.",
  reviewing: "검증·검토 단계입니다. 대기 중인 검토 지점에서 결정을 내려주세요.",
  completed: "보고서가 완료됐습니다. HWPX·PDF·Markdown으로 다운로드할 수 있습니다.",
  archived: "보관된 프로젝트입니다.",
};

export default function OverviewPage() {
  const { id: projectId = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { user, logout } = useAuth();

  const projectQuery = useProject(projectId);
  const updateConfig = useUpdateProjectConfig(projectId);
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
          onClick={() => navigate("/projects")}
        >
          <ArrowLeft className="mr-1 h-4 w-4" />
          프로젝트 목록
        </Button>

        {projectQuery.isLoading ? (
          <LoadingSkeleton variant="block" />
        ) : projectQuery.isError || !project ? (
          <EmptyState
            title="프로젝트를 찾을 수 없습니다"
            description="삭제됐거나 접근 권한이 없습니다."
            action={
              <Button variant="outline" onClick={() => navigate("/projects")}>
                목록으로
              </Button>
            }
          />
        ) : (
          <OverviewBody
            project={project}
            isUpdating={updateConfig.isPending}
            onSaveConfig={async (values) => {
              try {
                await updateConfig.mutateAsync(values.config);
                toast.success("프로젝트 옵션이 저장됐습니다.");
              } catch (err) {
                const msg = err instanceof ApiError ? err.message : "저장에 실패했습니다.";
                toast.error("저장 실패", { description: msg });
              }
            }}
          />
        )}
      </div>
    </AppShell>
  );
}

interface OverviewBodyProps {
  project: Project;
  isUpdating: boolean;
  onSaveConfig: (values: ProjectFormValues) => Promise<void>;
}

function OverviewBody({ project, isUpdating, onSaveConfig }: OverviewBodyProps) {
  const navigate = useNavigate();
  // 실측 사용량 — token_usage 테이블 합산(/projects/{id}/progress)
  const usageQuery = useProgressSnapshot(project.id);
  const tokensUsed = usageQuery.data?.tokens_used ?? 0;
  const costUsed = usageQuery.data?.cost_usd ?? 0;
  // 진행률: 개요 요약은 프로젝트 상태가 진실(완료=100%). /progress 스냅샷 percent는
  // 실행 중 진행 페이지가 만든 시나리오 러너 상태를 반영해 완료 프로젝트에서도 낮게
  // 나올 수 있으므로 여기선 쓰지 않는다(상세 실시간 진행은 진행 패널에서).
  const progressPct = STATUS_PERCENT[project.status];

  const deleteProject = useDeleteProject();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const canDelete = project.status === "completed" || project.status === "archived";
  const onDelete = async () => {
    try {
      await deleteProject.mutateAsync(project.id);
      toast.success("프로젝트가 삭제됐습니다.");
      navigate("/projects", { replace: true });
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "삭제에 실패했습니다.";
      toast.error("삭제 실패", { description: msg });
      setConfirmDelete(false);
    }
  };

  const editDefaults: ProjectFormValues = {
    title: project.title,
    topic: project.topic,
    config: project.config,
  };

  return (
    <>
      <header className="flex flex-col gap-3">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2">
              <Badge variant="secondary" className="font-mono">
                {presetLabel(project.preset)}
              </Badge>
              <StatusDot kind={STATUS_KIND[project.status]} label={STATUS_LABEL[project.status]} />
            </div>
            <h1 className="max-w-3xl text-3xl font-semibold text-fg">{project.title}</h1>
            <p className="max-w-3xl text-sm text-fg-secondary">{project.topic}</p>
            <dl className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-fg-tertiary">
              <Meta label="생성일" value={project.created_at.slice(0, 10)} />
              {project.updated_at ? (
                <Meta label="최근 수정" value={project.updated_at.slice(0, 10)} />
              ) : null}
              <Meta label="소유자" value={project.owner_name ?? project.owner_id} />
            </dl>
          </div>
          <PrimaryAction project={project} onNavigate={navigate} />
        </div>
      </header>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="flex flex-col gap-6">
          <Card>
            <CardHeader>
              <CardTitle>진행 요약</CardTitle>
              <CardDescription>{NEXT_STEP_HINT[project.status]}</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <div className="flex items-center justify-between text-xs text-fg-tertiary">
                <span>진행률</span>
                <span className="font-mono text-fg">{progressPct}%</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-bg-tertiary">
                <div
                  className="h-full bg-accent transition-[width]"
                  style={{ width: `${progressPct}%` }}
                />
              </div>
              <Button
                variant="outline"
                className="w-fit"
                onClick={() => navigate(`/projects/${project.id}/progress`)}
              >
                진행 패널에서 자세히 보기
                <ArrowRight className="ml-1 h-4 w-4" />
              </Button>
            </CardContent>
          </Card>

          <QuickActions project={project} onNavigate={navigate} />

          <Accordion type="single" collapsible>
            <AccordionItem value="config" className="rounded-lg border border-border bg-bg">
              <AccordionTrigger className="px-4 py-3 hover:no-underline">
                <span className="flex items-center gap-2 text-sm font-semibold text-fg">
                  <Settings2 className="h-4 w-4 text-fg-secondary" aria-hidden />
                  프로젝트 옵션
                </span>
              </AccordionTrigger>
              <AccordionContent className="px-4 pb-4">
                <ProjectConfigForm
                  mode="edit"
                  defaultValues={editDefaults}
                  submitting={isUpdating}
                  onSubmit={onSaveConfig}
                />
              </AccordionContent>
            </AccordionItem>
          </Accordion>
        </div>

        <aside className="flex flex-col gap-4 lg:sticky lg:top-6 lg:self-start">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">사용량</CardTitle>
              <CardDescription>실제 측정값 기준</CardDescription>
            </CardHeader>
            <CardContent>
              <dl className="grid grid-cols-2 gap-3">
                <div className="flex flex-col gap-0.5">
                  <dt className="text-xs text-fg-tertiary">사용 토큰</dt>
                  <dd className="font-mono text-base font-medium text-fg">
                    {tokensUsed.toLocaleString()}
                  </dd>
                </div>
                <div className="flex flex-col gap-0.5">
                  <dt className="text-xs text-fg-tertiary">사용 비용</dt>
                  <dd className="font-mono text-base font-medium text-fg">
                    ${costUsed.toFixed(2)}
                  </dd>
                </div>
              </dl>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">옵션 요약</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="flex flex-wrap gap-1.5">
                <Badge variant="secondary">분석 {project.config.enabled_analyzers.length}개</Badge>
                <Badge variant="secondary">
                  차별화{" "}
                  {
                    [
                      project.config.enable_pre_reconciliation,
                      project.config.enable_consistency_graph,
                      project.config.enable_dual_track_search,
                      project.config.enable_source_tagging,
                      project.config.enable_critic_agent,
                      project.config.enable_glossary,
                    ].filter(Boolean).length
                  }
                  개
                </Badge>
                <Badge variant="secondary">출력 {project.config.output_formats.length}개</Badge>
                <Badge variant="secondary">깊이 {project.config.depth_mode}</Badge>
              </div>
            </CardContent>
          </Card>

          {canDelete ? (
            <Button
              variant="outline"
              className="w-full border-border text-fg-danger hover:bg-bg-secondary"
              onClick={() => setConfirmDelete(true)}
              disabled={deleteProject.isPending}
            >
              <Trash2 className="mr-1 h-4 w-4" aria-hidden />
              프로젝트 삭제
            </Button>
          ) : null}
        </aside>
      </div>

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>프로젝트를 삭제할까요?</DialogTitle>
            <DialogDescription>
              "{project.title}" 프로젝트와 관련 데이터(자료·인덱스·검토 기록)가 영구 삭제됩니다.
              토큰 사용량·비용 기록은 남습니다. 이 작업은 되돌릴 수 없습니다.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setConfirmDelete(false)}
              disabled={deleteProject.isPending}
            >
              취소
            </Button>
            <Button
              variant="destructive"
              onClick={() => void onDelete()}
              disabled={deleteProject.isPending}
            >
              {deleteProject.isPending ? "삭제 중…" : "삭제"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
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

function PrimaryAction({
  project,
  onNavigate,
}: {
  project: Project;
  onNavigate: (to: string) => void;
}) {
  if (project.status === "created") {
    return (
      <Button size="lg" onClick={() => onNavigate(`/projects/${project.id}/progress`)}>
        <PlayCircle className="mr-1 h-4 w-4" />
        작성 시작
      </Button>
    );
  }
  if (project.status === "completed") {
    return (
      <Button size="lg" onClick={() => onNavigate(`/projects/${project.id}/export`)}>
        <Download className="mr-1 h-4 w-4" />
        HWPX 다운로드
      </Button>
    );
  }
  if (project.status === "archived") {
    return (
      <Button size="lg" variant="outline" disabled>
        보관된 프로젝트
      </Button>
    );
  }
  return (
    <Button size="lg" onClick={() => onNavigate(`/projects/${project.id}/progress`)}>
      진행 패널 보기
      <ArrowRight className="ml-1 h-4 w-4" />
    </Button>
  );
}

function QuickAction({
  icon: Icon,
  title,
  description,
  disabled,
  badge,
  onClick,
}: {
  icon: typeof PlayCircle;
  title: string;
  description: string;
  disabled?: boolean;
  badge?: { text: string; tone: "warning" | "muted" };
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex items-start gap-3 rounded border border-border bg-bg p-4 text-left transition-colors",
        disabled
          ? "cursor-not-allowed opacity-50"
          : "hover:border-border-strong hover:bg-bg-secondary",
      )}
    >
      <Icon className="mt-0.5 h-5 w-5 shrink-0 text-fg-secondary" aria-hidden />
      <div className="flex flex-1 flex-col gap-0.5">
        <span className="flex items-center gap-2 text-sm font-medium text-fg">
          {title}
          {badge ? (
            <span
              className={cn(
                "rounded-sm border px-1.5 py-0.5 text-[10px] font-normal",
                badge.tone === "warning"
                  ? "border-fg-warning/30 bg-bg-warning text-fg-warning"
                  : "border-border bg-bg-secondary text-fg-tertiary",
              )}
            >
              {badge.text}
            </span>
          ) : null}
        </span>
        <span className="text-xs text-fg-tertiary">{description}</span>
      </div>
    </button>
  );
}

function QuickActions({
  project,
  onNavigate,
}: {
  project: Project;
  onNavigate: (to: string) => void;
}) {
  const contradictions = useProjectContradictions(project.id);
  const pendingCount = contradictions.data?.items.filter((c) => c.status === "pending").length ?? 0;
  const totalCount = contradictions.data?.items.length ?? 0;

  return (
    <section>
      <h2 className="mb-3 text-base font-semibold text-fg">빠른 작업</h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <QuickAction
          icon={FileSearch}
          title="자료 검토"
          description="채택할 자료를 선택하고 추가 업로드"
          onClick={() => onNavigate(`/projects/${project.id}/sources`)}
        />
        <QuickAction
          icon={GitCompare}
          title="모순 해결"
          description="자료 간 충돌을 사용자가 결정"
          disabled={totalCount === 0}
          badge={
            pendingCount > 0
              ? { text: `${pendingCount}건 검토 필요`, tone: "warning" }
              : totalCount > 0
                ? { text: "모두 해결됨", tone: "muted" }
                : undefined
          }
          onClick={() => onNavigate(`/projects/${project.id}/reconcile`)}
        />
        <QuickAction
          icon={ScanLine}
          title="진행 상황"
          description="실시간 작성 진행을 모니터링"
          onClick={() => onNavigate(`/projects/${project.id}/progress`)}
        />
        <QuickAction
          icon={Eye}
          title="섹션 미리보기"
          description="작성된 본문을 챕터별로 확인"
          disabled={project.status === "created"}
          onClick={() => onNavigate(`/projects/${project.id}/preview`)}
        />
        <QuickAction
          icon={SquarePen}
          title="보고서 편집"
          description="3-패널 편집기 (텍스트 편집 본격 작동 준비 중)"
          badge={{ text: "준비 중", tone: "muted" }}
          disabled={project.status === "created"}
          onClick={() => onNavigate(`/projects/${project.id}/editor`)}
        />
        <QuickAction
          icon={Download}
          title="HWPX 다운로드"
          description="회사 표준 양식으로 출력"
          disabled={project.status !== "completed"}
          onClick={() => onNavigate(`/projects/${project.id}/export`)}
        />
      </div>
    </section>
  );
}
