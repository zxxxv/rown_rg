import {
  ArrowLeft,
  ArrowRight,
  Download,
  FileSearch,
  PlayCircle,
  Settings2,
  SquarePen,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { useProgressSnapshot } from "@/api/progress";
import {
  useDeleteProject,
  useProject,
  useRunProject,
  useUpdateProjectConfig,
} from "@/api/projects";
import { useProjectSections } from "@/api/sections";
import { useProjectSources } from "@/api/sources";
import type { Project, ProjectStatus } from "@/api/types";
import { useVerifyReport } from "@/api/verify";
import { StatusDot, type StatusKind } from "@/components/data-display/StatusDot";
import { EmptyState } from "@/components/feedback/EmptyState";
import { PageLoading } from "@/components/feedback/PageLoading";
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
import { env } from "@/env";
import { useDownload } from "@/features/export/useDownload";
import { PipelineStepper } from "@/features/progress/PipelineStepper";
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
  cancelled: "취소됨",
};

const STATUS_KIND: Record<ProjectStatus, StatusKind> = {
  created: "tertiary",
  researching: "info",
  indexing: "info",
  writing: "info",
  reviewing: "warning",
  completed: "success",
  archived: "tertiary",
  cancelled: "danger",
};

const DEPTH_LABEL: Record<string, string> = {
  outline_only: "개요만",
  standard: "표준",
  full_report: "보고서 전체",
  deep_dive: "심층 분석",
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
          <PageLoading label="프로젝트를 불러오는 중…" />
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
  // 실측 사용량 + 스테퍼 입력 — /progress 스냅샷. 종결 상태가 아니면 폴링으로
  // 단계 전이·게이트 개방을 따라잡는다(개요가 실행을 지켜보는 화면이 되도록).
  const terminal = ["completed", "archived"].includes(project.status);
  const usageQuery = useProgressSnapshot(project.id, true, {
    refetchInterval: terminal ? false : 7_000,
  });
  const tokensUsed = usageQuery.data?.tokens_used ?? 0;
  const costUsed = usageQuery.data?.cost_usd ?? 0;
  const deleteProject = useDeleteProject();
  const [confirmDelete, setConfirmDelete] = useState(false);
  // 삭제는 항상 노출 — 백엔드가 '실행 중인 순간'만 막는다(게이트 대기·실패 잔류 정리 가능).
  const canDelete = true;
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
          <PrimaryAction project={project} />
        </div>
      </header>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="flex flex-col gap-6">
          {/* 진행 요약(% 바) 카드는 제거됨 — 폴링 없는 project.status 기반이라
              우측 스테퍼(7초 폴링)와 어긋났고, 스테퍼가 위치·다음 행동을 다 보여준다. */}
          {/* 완성 후엔 요약 카드가 '숫자 달린 빠른 작업'을 겸한다 — 둘을 같이
              보여주면 진입점이 겹친다(본문↔미리보기, 채택 자료↔자료 검토). */}
          {project.status === "completed" ? (
            <CompletedSummaryCard project={project} onNavigate={navigate} />
          ) : (
            <QuickActions project={project} onNavigate={navigate} />
          )}

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
          <PipelineStepper projectId={project.id} snapshot={usageQuery.data} />

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
              {/* 실제 생성에 소비되는 옵션만 표시 — 분석(enabled_analyzers)은 절별
                  에이전트 배정으로 이관된 레거시(항상 0), 차별화·출력은 미배선 장식. */}
              <div className="flex flex-wrap gap-1.5">
                <Badge variant="secondary">
                  목차 ·{" "}
                  {project.config.outline
                    ? `${project.config.outline.chapters.reduce((n, c) => n + c.sections.length, 0)}절`
                    : "미구성"}
                </Badge>
                <Badge variant="secondary">
                  깊이 · {DEPTH_LABEL[project.config.depth_mode] ?? project.config.depth_mode}
                </Badge>
                <Badge variant="secondary">
                  모델 · {project.config.model_mode === "economy" ? "절약(Haiku)" : "표준(Sonnet)"}
                </Badge>
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

// 완성된 프로젝트의 결과 요약 — 개요 본문이 텅 비지 않게, 산출물의 핵심 숫자를
// 실데이터로 보여주고 각 타일에서 해당 작업 화면으로 바로 이동한다.
function CompletedSummaryCard({
  project,
  onNavigate,
}: {
  project: Project;
  onNavigate: (to: string) => void;
}) {
  const sections = useProjectSections(project.id);
  const sources = useProjectSources(project.id);
  const verify = useVerifyReport(project.id);

  const nSections = sections.data?.tree.reduce((n, ch) => n + ch.children.length, 0) ?? null;
  const nChapters = sections.data?.tree.length ?? null;
  const nAdopted = sources.data?.items.filter((s) => s.is_included !== false).length ?? null;
  const findings = verify.data ?? null;
  const nCritical = findings?.filter((f) => f.severity === "critical").length ?? 0;

  const tiles: { label: string; value: string; hint: string; to?: string; warn?: boolean }[] = [
    {
      label: "본문",
      value: nSections !== null ? `${nChapters}장 ${nSections}절` : "-",
      hint: "미리보기·편집 열기",
      to: `/projects/${project.id}/preview`,
    },
    {
      label: "채택 자료",
      value: nAdopted !== null ? `${nAdopted}건` : "-",
      hint: "자료 목록 열기",
      to: `/projects/${project.id}/sources`,
    },
    {
      label: "PM 검증 경고",
      value: findings !== null ? `${findings.length}건` : "-",
      hint: nCritical > 0 ? `critical ${nCritical} - 확인 권장` : "납품 전 참고",
      to: `/projects/${project.id}/preview`,
      warn: nCritical > 0,
    },
    {
      label: "완료일",
      value: (project.updated_at ?? project.created_at).slice(0, 10),
      hint: "회사 표준 양식 (함초롬바탕 11pt)",
    },
  ];

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">보고서 요약</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {tiles.map((t) => {
            const inner = (
              <>
                <span className="text-xs text-fg-tertiary">{t.label}</span>
                <span className="text-lg font-semibold text-fg">{t.value}</span>
                <span
                  className={cn(
                    "flex items-center gap-1 text-xs",
                    t.warn ? "text-fg-warning" : t.to ? "text-fg-info" : "text-fg-tertiary",
                  )}
                >
                  {t.hint}
                  {t.to ? <ArrowRight className="h-3 w-3" aria-hidden /> : null}
                </span>
              </>
            );
            const base = "flex flex-col gap-1 rounded border p-3 text-left";
            const tone = t.warn ? "border-fg-warning/40 bg-bg-warning" : "border-border bg-bg";
            return t.to ? (
              <button
                key={t.label}
                type="button"
                onClick={() => t.to && onNavigate(t.to)}
                className={cn(
                  base,
                  tone,
                  "transition-colors hover:border-border-strong hover:bg-bg-secondary",
                )}
              >
                {inner}
              </button>
            ) : (
              <div key={t.label} className={cn(base, tone)}>
                {inner}
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
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

function PrimaryAction({ project }: { project: Project }) {
  // 진행 페이지 폐지 — 시작은 개요에서 바로 실행하고, 진행 관찰은 우측
  // 진행 단계 스테퍼가 담당한다(7초 폴링으로 researching 전이를 따라잡음).
  const run = useRunProject();
  const download = useDownload();
  const startRun = () => {
    run.mutate(project.id, {
      onSuccess: () => toast.success("자료 조사를 시작했습니다."),
      onError: (err: unknown) => {
        const msg = err instanceof ApiError ? err.message : "시작에 실패했습니다.";
        if (msg.includes("이미 실행")) {
          toast.info("이미 실행 중입니다");
          return;
        }
        toast.error("자료 조사 시작 실패", { description: msg });
      },
    });
  };

  if (project.status === "created" || project.status === "cancelled") {
    return (
      <Button size="lg" onClick={startRun} disabled={run.isPending}>
        <PlayCircle className="mr-1 h-4 w-4" />
        {run.isPending
          ? "시작 중…"
          : project.status === "cancelled"
            ? "자료 조사 다시 시작"
            : "자료 조사 시작"}
      </Button>
    );
  }
  if (project.status === "completed") {
    // '다운로드' 버튼은 즉시 다운로드해야 한다 — 페이지 이동이면 이름이 거짓말
    // (출력 상세·검증 배너는 요약 카드의 '완료일' 타일로 진입).
    return (
      <Button
        size="lg"
        onClick={() =>
          download({
            url: `${env.VITE_API_BASE_URL.replace(/\/$/, "")}/projects/${project.id}/export`,
            filename: `${project.title}.hwpx`,
            label: "HWPX",
          })
        }
      >
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
  // 실행 중 — 별도 CTA 없음(우측 스테퍼가 현재 위치·다음 행동을 안내)
  return null;
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
  {
    /* '모순 해결' 카드는 페이지(reconcile)와 함께 제거됨(2026-08-04) —
       문서 횡단 검증은 PM 검증(verify_findings)이 실물로 수행한다. */
  }
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
        {/* '보고서 편집' 별도 카드는 제거됨 — 편집(직접 수정·AI 재작성)이
            미리보기 화면에 통합돼 있어 '준비 중' 표기가 낡은 중복이었다. */}
        <QuickAction
          icon={SquarePen}
          title="미리보기·편집"
          description="본문 확인, 직접 수정, AI 재작성"
          disabled={project.status === "created"}
          onClick={() => onNavigate(`/projects/${project.id}/preview`)}
        />
        {/* 'HWPX 다운로드' 카드는 제거됨 — 완료 시 헤더 CTA가 같은 출력 페이지로
            이동하는 중복 진입점이었다(진입점 정리 원칙). */}
      </div>
    </section>
  );
}
