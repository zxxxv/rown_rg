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
} from "lucide-react";
import { useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { useProjectContradictions } from "@/api/contradictions";
import { useProject, useUpdateProjectConfig } from "@/api/projects";
import type { Project, ProjectStatus } from "@/api/types";
import { CostEstimate } from "@/components/data-display/CostEstimate";
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
import { estimate } from "@/features/project-config/estimator";
import { ProjectConfigForm } from "@/features/project-config/ProjectConfigForm";
import { PRESET_LABEL } from "@/features/project-config/presets";
import type { ProjectFormValues } from "@/features/project-config/schema";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";

const STATUS_LABEL: Record<ProjectStatus, string> = {
  draft: "초안",
  researching: "자료조사 중",
  indexing: "인덱싱 중",
  writing: "작성 중",
  qa: "검증 중",
  review: "사용자 검토 대기",
  completed: "완료",
  archived: "보관",
  failed: "실패",
};

const STATUS_KIND: Record<ProjectStatus, StatusKind> = {
  draft: "tertiary",
  researching: "info",
  indexing: "info",
  writing: "info",
  qa: "info",
  review: "warning",
  completed: "success",
  archived: "tertiary",
  failed: "danger",
};

const NEXT_STEP_HINT: Record<ProjectStatus, string> = {
  draft: "옵션 검토 후 작성을 시작하세요.",
  researching: "AI가 자료를 수집·평가하고 있습니다.",
  indexing: "수집한 자료를 청크·임베딩으로 변환 중입니다.",
  writing: "Level 1~4 본문을 작성 중입니다.",
  qa: "Fact·Consistency·Style·Critic 4단계 검증을 수행 중입니다.",
  review: "검토 지점에서 사용자 결정을 기다리는 중입니다.",
  completed: "보고서가 완료됐습니다. HWPX·PDF·Markdown으로 다운로드할 수 있습니다.",
  archived: "보관된 프로젝트입니다.",
  failed: "작업이 실패했습니다. 진행 패널에서 원인을 확인하세요.",
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
  const estimateValue = useMemo(() => estimate(project.config), [project.config]);
  const usedFraction = project.status === "draft" ? 0 : ((project.progress ?? 0) / 100) * 0.95;
  const tokensUsed = Math.round(estimateValue.estimatedTokens * usedFraction);
  const costUsed = Math.round(estimateValue.estimatedCostUsd * usedFraction);

  const editDefaults: ProjectFormValues = {
    title: project.title,
    topic: project.topic,
    config: project.config,
    ...(project.deadline ? { deadline: project.deadline } : {}),
  };

  return (
    <>
      <header className="flex flex-col gap-3">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2">
              <Badge variant="secondary" className="font-mono">
                {PRESET_LABEL[project.preset]}
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
              <Meta label="마감일" value={project.deadline ?? "—"} />
              <Meta label="소유자" value={project.owner_id} />
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
                <span className="font-mono text-fg">{project.progress ?? 0}%</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-bg-tertiary">
                <div
                  className="h-full bg-accent transition-[width]"
                  style={{ width: `${project.progress ?? 0}%` }}
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
                  프로젝트 옵션 변경
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
              <CardTitle className="text-base">예상 견적</CardTitle>
              <CardDescription>현재 옵션 기준</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              <CostEstimate
                estimatedHours={estimateValue.estimatedHours}
                estimatedTokens={estimateValue.estimatedTokens}
                estimatedCostUsd={estimateValue.estimatedCostUsd}
              />
              <div className="flex justify-between border-t border-border pt-3 text-xs">
                <span className="text-fg-tertiary">현재까지</span>
                <span className="font-mono text-fg-secondary">
                  {tokensUsed.toLocaleString()} 토큰 · ${costUsed}
                </span>
              </div>
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
        </aside>
      </div>
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
  if (project.status === "draft") {
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
          disabled={project.status === "draft"}
          onClick={() => onNavigate(`/projects/${project.id}/preview`)}
        />
        <QuickAction
          icon={SquarePen}
          title="보고서 편집"
          description="3-패널 편집기 (Phase 5 본격 작동 예정)"
          badge={{ text: "Phase 5", tone: "muted" }}
          disabled={project.status === "draft"}
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
