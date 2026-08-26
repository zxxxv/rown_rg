import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  BadgeCheck,
  Download,
  FileSearch,
  FolderSync,
  Lightbulb,
  PieChart,
  PlayCircle,
  Settings2,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import {
  type PendingGate,
  progressKeys,
  useConfirmedStalled,
  useProgressSnapshot,
} from "@/api/progress";
import {
  projectKeys,
  useDeleteProject,
  useProject,
  useRunProject,
  useUpdateProjectConfig,
} from "@/api/projects";
import type { Project, ProjectStatus } from "@/api/types";
import { useFinalizeProject, useReportVersions, useUnfinalizeProject } from "@/api/versions";
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { env } from "@/env";
import { UnreflectedCard } from "@/features/export/UnreflectedCard";
import { useDownload } from "@/features/export/useDownload";
import { PipelineStepper } from "@/features/progress/PipelineStepper";
import { StageLine } from "@/features/progress/StageLine";
import { StatePanel } from "@/features/progress/StatePanel";
import { ProjectConfigForm } from "@/features/project-config/ProjectConfigForm";
import { presetLabel } from "@/features/project-config/presets";
import type { ProjectFormValues } from "@/features/project-config/schema";
import { SourceUsageCard } from "@/features/stats/SourceUsageCard";
import { ReopenDialog } from "@/features/versions/ReopenDialog";
import { useAuth } from "@/hooks/useAuth";
import { ReportWorkspace } from "@/pages/projects/[id]/preview";

// 게이트별 결정 화면과 CTA 문구. 백엔드 runner._GATE_PAGES와 같은 대응이다.
// qa_select는 레거시 - 게이트 제거 전 백엔드로 작성돼 아직 pending인 프로젝트만 온다.
const GATE_CTA: Record<string, { page: string; label: string }> = {
  design_brief: { page: "brief", label: "설계 검토 완료하러 가기" },
  source_pool: { page: "sources", label: "자료 검토 완료하러 가기" },
  qa_select: { page: "preview", label: "본문 검토 완료하러 가기" },
};

const MODEL_MODE_LABEL: Record<string, string> = {
  economy: "절약(Haiku + GPT-mini)",
  standard: "표준(Haiku 수집 + Sonnet 작성)",
  premium: "고급(Haiku 수집 + Opus 5 작성)",
};

const MODEL_MODE_NAME: Record<string, string> = {
  economy: "절약",
  standard: "표준",
  premium: "고급",
};

// 모델 id -> 표시명. 카탈로그에 없는 id는 원문 그대로 보여준다(거짓 라벨보다 낫다).
const MODEL_SHORT_NAME: Record<string, string> = {
  "claude-haiku-4-5": "Haiku",
  "claude-sonnet-4-6": "Sonnet",
  "claude-sonnet-5": "Sonnet 5",
  "claude-opus-5": "Opus 5",
  "gpt-5.4-mini-2026-03-17": "GPT-mini",
};

// 라벨은 실행 시점 스냅샷(config.models) 우선 - 모드 매핑은 배포로 바뀔 수 있어
// 정적 라벨만 쓰면 과거 런의 표시가 함께 바뀐다(예: 옛 고급 런은 Sonnet 수집이었다).
function modelModeLabel(config: { model_mode: string; models?: Record<string, string> }): string {
  const research = config.models?.research;
  const write = config.models?.write;
  if (research && write) {
    const mode = MODEL_MODE_NAME[config.model_mode] ?? config.model_mode;
    const short = (id: string) => MODEL_SHORT_NAME[id] ?? id;
    return `${mode}(${short(research)} 수집 + ${short(write)} 작성)`;
  }
  return MODEL_MODE_LABEL[config.model_mode] ?? config.model_mode;
}

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
  // 맨 위 배지용 - 지금 원고가 어느 버전인지. 목록은 최신부터라 [0]이 마지막 버전이고,
  // 어느 버전도 지금 본문과 같지 않으면 그 뒤로 손을 댄 것이다.
  const versionsQuery = useReportVersions(project.id);
  const latestVersion = versionsQuery.data?.[0] ?? null;
  const versionDirty = Boolean(
    versionsQuery.data?.length && !versionsQuery.data.some((v) => v.is_current),
  );
  const navigate = useNavigate();
  // 실측 사용량 + 스테퍼 입력 - /progress 스냅샷. 종결 상태가 아니면 폴링으로
  // 단계 전이·게이트 개방을 따라잡는다(개요가 실행을 지켜보는 화면이 되도록).
  const terminal = ["completed", "archived"].includes(project.status);
  const usageQuery = useProgressSnapshot(project.id, true, {
    refetchInterval: terminal ? false : 7_000,
  });
  // 죽은 런(백엔드 태스크 소실) - 스피너 대신 중단 안내·재개 CTA로 바꾼다.
  const stalled = useConfirmedStalled(usageQuery.data);
  const tokensUsed = usageQuery.data?.tokens_used ?? 0;
  const costUsed = usageQuery.data?.cost_usd ?? 0;
  const deleteProject = useDeleteProject();
  const finalize = useFinalizeProject(project.id);
  const unfinalize = useUnfinalizeProject(project.id);
  const [confirmDelete, setConfirmDelete] = useState(false);
  // 아코디언을 제어형으로 - 상태 패널의 "목차 편집"이 여기를 펴야 한다.
  const [openPanel, setOpenPanel] = useState<string | undefined>(undefined);
  // 삭제는 항상 노출 - 백엔드가 '실행 중인 순간'만 막는다(게이트 대기·실패 잔류 정리 가능).
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
              {/* 상태 배지 2종(단계 점 + 검토 중/최종 확정)은 걷어냈다 - 바로 아래
                  상태 줄이 같은 말을 더 크게 한다. 작은 글씨로 두 번 말하면 훑을 때
                  둘 다 안 읽힌다(2026-08-26 지적). */}
            </div>
            <h1 className="max-w-3xl text-3xl font-semibold text-fg">{project.title}</h1>
            <p className="max-w-3xl text-sm text-fg-secondary">{project.topic}</p>
            {/* 지금 어디까지 왔고 다음에 뭘 하면 되나 - 여러 화면을 오가며 고치다 보면
                상태가 헷갈린다는 지적(2026-08-26). 산출물에서 센 값만 쓴다. */}
            <div className="max-w-3xl">
              <StageLine project={project} snapshot={usageQuery.data} />
            </div>
            <dl className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-xs text-fg-tertiary">
              <Meta label="생성일" value={project.created_at.slice(0, 10)} />
              {project.updated_at ? (
                <Meta label="최근 수정" value={project.updated_at.slice(0, 10)} />
              ) : null}
              <Meta label="소유자" value={project.owner_name ?? project.owner_id} />
            </dl>
            {/* 사용량·옵션은 '읽고 마는' 메타라 우측 기둥을 차지할 이유가 없다 -
                헤더에 가로 타일로 붙여 본문 작업공간에 폭을 내준다(2026-08-09). */}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 pt-1">
              <span className="text-xs text-fg-tertiary">
                사용 토큰{" "}
                <b className="font-mono text-sm font-medium text-fg">
                  {tokensUsed.toLocaleString()}
                </b>
              </span>
              <span className="text-xs text-fg-tertiary">
                비용 <b className="font-mono text-sm font-medium text-fg">${costUsed.toFixed(2)}</b>
              </span>
              {project.total_chars ? (
                // 분량이 목표에 닿았는지가 이 화면의 핵심 질문이다 - 토큰·비용 옆에 둔다.
                <span className="text-xs text-fg-tertiary">
                  본문{" "}
                  <b className="font-mono text-sm font-medium text-fg">
                    {project.total_chars.toLocaleString()}자
                  </b>
                </span>
              ) : null}
              <span className="h-3 w-px bg-border" aria-hidden />
              {/* 지금 원고가 어느 버전인가 - 버전이 비교할 때만 쓰이는 값이 아니라
                  "지금 보고 있는 게 뭔지"를 말하는 값이라 맨 위에 둔다(2026-08-27).
                  마지막 스냅샷 이후 손을 댔으면 그 사실까지 말한다 - 번호만 적으면
                  얼려 둔 v6과 지금 원고가 같다는 거짓말이 된다. */}
              {latestVersion ? (
                <Badge
                  variant="secondary"
                  className="font-mono"
                  title={
                    versionDirty
                      ? "마지막으로 얼린 버전 이후에 본문이 바뀌었습니다 - 지금 원고는 어느 버전과도 같지 않습니다"
                      : "지금 원고가 이 버전과 같습니다"
                  }
                >
                  v{latestVersion.version_no}
                  {versionDirty ? " + 수정됨" : ""}
                </Badge>
              ) : null}
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
                {/* 역할별로 모델이 다르다 - 'Haiku'로만 적으면 사실과 다르다(본문은 GPT-5.4-mini) */}
                모델 · {modelModeLabel(project.config)}
              </Badge>
            </div>
          </div>
          {/* 주 CTA와 삭제를 한 줄에 - 파괴적 동작이라 ghost로 낮춰 무게 차이를 준다 */}
          <div className="flex items-center gap-2">
            <PrimaryAction
              project={project}
              pendingGate={usageQuery.data?.pending_gate ?? null}
              stalled={stalled}
              liveStatus={usageQuery.data?.status}
            />
            {project.status === "completed" ? (
              project.finalized_at ? (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={unfinalize.isPending}
                  onClick={() => {
                    unfinalize.mutate(undefined, {
                      onSuccess: () => toast("확정을 해제했습니다 - 다시 검토 중입니다."),
                      onError: () => toast.error("확정 해제에 실패했습니다."),
                    });
                  }}
                  title="본문과 버전은 그대로 두고 확정 표식만 내립니다"
                >
                  확정 해제
                </Button>
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={finalize.isPending}
                  onClick={() => {
                    finalize.mutate(undefined, {
                      onSuccess: () =>
                        toast.success("최종 확정됨", {
                          description: "확정 시점의 본문이 버전으로 보존됩니다.",
                        }),
                      onError: () => toast.error("확정에 실패했습니다."),
                    });
                  }}
                  title="이 내용을 납품 확정본으로 선언하고 버전으로 보존합니다"
                >
                  <BadgeCheck className="mr-1 h-4 w-4" aria-hidden />
                  최종 확정
                </Button>
              )
            ) : null}
            {canDelete ? (
              <Button
                variant="ghost"
                size="sm"
                className="text-fg-danger hover:bg-bg-secondary"
                onClick={() => setConfirmDelete(true)}
                disabled={deleteProject.isPending}
              >
                <Trash2 className="mr-1 h-4 w-4" aria-hidden />
                삭제
              </Button>
            ) : null}
          </div>
        </div>
      </header>

      <div className="grid grid-cols-1 gap-6 min-[1440px]:grid-cols-[minmax(0,1fr)_280px]">
        <div className="flex flex-col gap-6">
          {/* 진행 요약(% 바) 카드는 제거됨 - 폴링 없는 project.status 기반이라
              우측 스테퍼(7초 폴링)와 어긋났고, 스테퍼가 위치·다음 행동을 다 보여준다. */}
          {/* 완성 후엔 요약 카드가 '숫자 달린 빠른 작업'을 겸한다 - 둘을 같이
              보여주면 진입점이 겹친다(본문↔미리보기, 채택 자료↔자료 검토). */}
          {/* 완료 요약 카드는 제거 - 본문이 같은 화면에 인라인이라 '보고서 열기'
              타일이 자기 자신을 가리켰다(2026-08-09 사용자 지적). 숫자는 헤더 타일과
              PM 경고 배너가 이미 보여준다. */}
          {/* '설계 검토'·'자료 검토' 바로가기 카드는 제거했다 - 우측 상태 패널이
              같은 문을 이미 갖고 있어 화면에 진입점이 두 벌이었다(2026-08-27 지적).
              자료는 완전한 중복이었고(둘 다 /sources), 설계는 목적지가 달라서
              (설계 검토 화면 / 목차 편집) 더 헷갈렸다 - 이제 상태 패널의 '설계' 줄이
              그 둘을 나란히 갖는다. 진입점은 한 곳에 모은다. */}

          <Accordion type="single" collapsible value={openPanel} onValueChange={setOpenPanel}>
            <AccordionItem
              value="config"
              id="project-config"
              className="rounded-lg border border-border bg-bg"
            >
              {/* 이름을 '이 보고서 > 설계'의 버튼과 맞춘다. 버튼은 '목차 편집'이라
                  하고 여기는 '프로젝트 옵션'이라 하니, 같은 자리를 여는 문이 둘로
                  보였다(2026-08-27 지적). 안에 목차 편집기와 옵션이 함께 있으므로
                  이름이 둘 다를 말해야 한다. */}
              <AccordionTrigger className="px-4 py-3 hover:no-underline">
                <span className="flex items-center gap-2 text-sm font-semibold text-fg">
                  <Settings2 className="h-4 w-4 text-fg-secondary" aria-hidden />
                  목차 편집 · 프로젝트 옵션
                </span>
              </AccordionTrigger>
              <AccordionContent className="px-4 pb-4">
                {/* 완료여도 잠그지 않는다 - 보고서는 완주가 끝이 아니고, 고친 내용이
                    본문에 닿았는지는 '미반영'으로 드러난다(2026-08-25). 백엔드
                    _CONFIG_FROZEN_STATUSES도 보관본만 막는다. */}
                <ProjectConfigForm
                  mode="edit"
                  frozen={project.status === "archived"}
                  defaultValues={editDefaults}
                  submitting={isUpdating}
                  onSubmit={onSaveConfig}
                />
              </AccordionContent>
            </AccordionItem>
            {/* 자료 사용 통계 - 번호 해석이 조립 후 규약이라 완성 보고서에서만 연다.
                근거 추적(블록 단위 미시)과 달리 보고서 전체의 자료 의존 구조를 조감한다. */}
            {project.status === "completed" || project.status === "archived" ? (
              <AccordionItem
                value="source-usage"
                className="mt-3 rounded-lg border border-border bg-bg"
              >
                <AccordionTrigger className="px-4 py-3 hover:no-underline">
                  <span className="flex items-center gap-2 text-sm font-semibold text-fg">
                    <PieChart className="h-4 w-4 text-fg-secondary" aria-hidden />
                    자료 사용 통계
                  </span>
                </AccordionTrigger>
                <AccordionContent className="px-4 pb-4">
                  <SourceUsageCard projectId={project.id} />
                </AccordionContent>
              </AccordionItem>
            ) : null}
          </Accordion>

          {/* 미반영 - 설계를 고쳤는데 본문이 아직 안 담은 절. 본문 바로 위에 둔다:
              내려가기 전에 "지금 보는 본문이 최신 설계가 아니다"를 먼저 알아야 한다. */}
          {project.status !== "created" ? <UnreflectedCard projectId={project.id} /> : null}

          {/* 보고서 본문 - 좌측 흐름 안에 두어야 진행 단계 카드 옆이 비지 않는다
              (2026-08-09: 그리드 밖에 두니 중앙이 붕 떴다는 사용자 지적). */}
          {project.status !== "created" ? <ReportWorkspace projectId={project.id} /> : null}
        </div>

        {/* 우측 기둥은 '지금 무슨 일이 벌어지는가'만 - 사용량·옵션은 헤더로,
            삭제는 페이지 맨 아래로 옮겨 본문 작업공간에 자리를 내줬다. */}
        {/* 런이 도는 중이면 '지금 뭐가 돌고 있나'(선형 스테퍼), 멈춰 있으면
            '무엇이 준비됐고 뭘 할 수 있나'(상태 패널). 완주 뒤로는 스테퍼가 전부
            체크된 채 굳어 정보를 못 주는데, 정작 그때부터가 손보는 구간이다
            (2026-08-25). 판정은 status가 아니라 runner_alive로 한다. */}
        <aside className="lg:sticky lg:top-6 lg:self-start">
          {usageQuery.data?.runner_alive || usageQuery.data?.pending_gate ? (
            <PipelineStepper projectId={project.id} snapshot={usageQuery.data} stalled={stalled} />
          ) : (
            <StatePanel
              project={project}
              snapshot={usageQuery.data}
              onOpenConfig={() => {
                setOpenPanel("config");
                document.getElementById("project-config")?.scrollIntoView({ behavior: "smooth" });
              }}
            />
          )}
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

// 완성된 프로젝트의 결과 요약 - 개요 본문이 텅 비지 않게, 산출물의 핵심 숫자를
// 실데이터로 보여주고 각 타일에서 해당 작업 화면으로 바로 이동한다.
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
  pendingGate,
  stalled = false,
  liveStatus,
}: {
  project: Project;
  pendingGate: PendingGate | null;
  stalled?: boolean;
  /** 진행 스냅샷의 상태(7초 폴링). 프로젝트 상세 스냅샷은 /run 뒤에도 한동안 낡아
   * 있어서, 실행이 시작됐는데도 버튼이 '자료 조사 시작'으로 남았다 - 오른쪽 스테퍼는
   * 이미 진행 중을 그리는데 버튼만 어긋나 보였다(2026-08-20 사용자 지적).
   * 서버가 진실이므로 폴링 값이 있으면 그쪽을 따른다. */
  liveStatus?: ProjectStatus;
}) {
  // 진행 페이지 폐지 - 시작은 개요에서 바로 실행하고, 진행 관찰은 우측
  // 진행 단계 스테퍼가 담당한다(7초 폴링으로 researching 전이를 따라잡음).
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const run = useRunProject();
  const { download, pending: downloading } = useDownload();
  const [reopening, setReopening] = useState(false);
  // 같은 버튼이 '처음 시작'과 '멈춘 런 이어받기' 둘 다를 처리한다 - 안내 문구가
  // 실제 동작과 어긋나면 사용자가 수집이 다시 도는 줄 안다(2026-08-09 보고).
  const status = liveStatus ?? project.status;
  const isResume = status !== "created" && status !== "cancelled";
  const startRun = () => {
    run.mutate(project.id, {
      onSuccess: () => {
        // 폴링(7초)을 기다리지 않고 둘 다 즉시 갱신 - 버튼과 스테퍼가 서로 다른
        // 스냅샷을 보고 어긋나던 자리다.
        void queryClient.invalidateQueries({ queryKey: projectKeys.detail(project.id) });
        void queryClient.invalidateQueries({ queryKey: progressKeys.snapshot(project.id) });
        toast.success(isResume ? "멈춘 지점부터 이어서 진행합니다." : "자료 조사를 시작했습니다.");
      },
      onError: (err: unknown) => {
        const msg = err instanceof ApiError ? err.message : "시작에 실패했습니다.";
        // 문구가 아니라 코드로 판정한다 - 메시지 문장이 바뀌면 조용히 빗나간다.
        if (err instanceof ApiError && err.code === "ALREADY_RUNNING") {
          toast.info("이미 진행 중입니다", {
            description: "지금 돌고 있는 작업이 끝나면 다음 단계로 넘어갑니다.",
          });
          return;
        }
        if (err instanceof ApiError && err.code === "GATE_PENDING") {
          // 스냅샷이 낡아 재개 버튼이 잠깐 남는 레이스 - 서버가 진실이므로 즉시
          // 갱신해 버튼을 검토 CTA로 바꾸고, 사용자를 검토 완료로 유도한다.
          void queryClient.invalidateQueries({ queryKey: progressKeys.snapshot(project.id) });
          toast.info("검토 대기 중입니다", {
            description: "검토를 완료(승인)하면 다음 단계가 이어서 진행됩니다.",
          });
          return;
        }
        toast.error(isResume ? "이어서 진행하지 못했습니다" : "자료 조사 시작 실패", {
          description: msg,
        });
      },
    });
  };

  if (status === "created" || status === "cancelled") {
    return (
      <Button size="lg" onClick={startRun} disabled={run.isPending}>
        <PlayCircle className="mr-1 h-4 w-4" />
        {run.isPending
          ? "시작 중…"
          : status === "cancelled"
            ? "자료 조사 다시 시작"
            : "자료 조사 시작"}
      </Button>
    );
  }
  if (status === "completed") {
    // '다운로드' 버튼은 즉시 다운로드해야 한다 - 페이지 이동이면 이름이 거짓말
    // (출력 상세·검증 배너는 요약 카드의 '완료일' 타일로 진입).
    // '다시 열기'는 시차 작성 진입점 - 현재 완성본을 버전으로 얼리고 자료 단계로
    // 되돌린다(2026-08-21 설계). 실행은 자료·목차를 손본 뒤 사람이 시작한다.
    return (
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="lg"
          disabled={downloading}
          onClick={() =>
            void download({
              url: `${env.VITE_API_BASE_URL.replace(/\/$/, "")}/projects/${project.id}/export`,
              filename: `${project.title}.hwpx`,
              label: "HWPX",
            })
          }
        >
          <Download className="mr-1 h-4 w-4" />
          {downloading ? "준비 중…" : "HWPX 다운로드"}
        </Button>
        <Button
          size="lg"
          variant="outline"
          onClick={() => navigate(`/projects/${project.id}/insights`)}
          title="시사점·제언을 2~3쪽으로 압축한 요약 - 본문 파일과 별개의 한글 파일로 받습니다"
        >
          <Lightbulb className="mr-1 h-4 w-4" />
          시사점 요약
        </Button>
        {/* '다시 열기'는 **확정된 보고서**에만 뜬다. 확정 전에는 이미 열려 있는
            문서라(절 편집·재작성·목차 수정이 다 되는 "검토 중"), 버튼이 있으면
            "지금은 잠겨 있나?"라는 없는 의문을 만든다(2026-08-26 지적). */}
        {project.finalized_at ? (
          <>
            <Button
              size="lg"
              variant="outline"
              onClick={() => setReopening(true)}
              title="확정을 풀고 자료를 보강해 이어 씁니다 - 지금 확정본은 버전으로 보관"
            >
              <FolderSync className="mr-1 h-4 w-4" />
              다시 열기
            </Button>
            <ReopenDialog projectId={project.id} open={reopening} onOpenChange={setReopening} />
          </>
        ) : null}
      </div>
    );
  }
  if (status === "archived") {
    return (
      <Button size="lg" variant="outline" disabled>
        보관된 프로젝트
      </Button>
    );
  }
  // 게이트 대기 - 재개가 아니라 검토 완료(승인)가 다음 행동이다. 여기서 '이어서 진행'을
  // 누르면 게이트를 건너뛰고 미확정 자료로 본문이 작성되던 버그(2026-08-12)가 있어,
  // 백엔드도 GATE_PENDING(422)으로 막는다. 재개 버튼은 숨기고 검토 화면 CTA만 노출.
  if (pendingGate) {
    const { page, label } = GATE_CTA[pendingGate.gate] ?? GATE_CTA.source_pool;
    return (
      <Button size="lg" onClick={() => navigate(`/projects/${project.id}/${page}`)}>
        <FileSearch className="mr-1 h-4 w-4" />
        {label}
      </Button>
    );
  }
  // 작업 단계 - 정상 실행 중이면 스테퍼가 안내하지만, 프로세스 재시작·자원 고갈로
  // 실행이 사라지면 화면상 '진행 중'인 채 영원히 멈춘다(2026-08-09 실사고). 이어받기
  // 버튼을 항상 열어두고, 실제로 살아 있으면 백엔드가 '이미 실행 중'으로 되돌린다.
  // 죽은 런이 확정되면(runner_alive=false 연속 관측) 주 CTA로 승격해 즉시 눈에 띈다.
  if (stalled) {
    return (
      <Button size="lg" onClick={startRun} disabled={run.isPending}>
        <PlayCircle className="mr-1 h-4 w-4" />
        {run.isPending ? "재개 중…" : "실행이 중단됨 - 이어서 재개"}
      </Button>
    );
  }
  return (
    <Button size="lg" variant="outline" onClick={startRun} disabled={run.isPending}>
      <PlayCircle className="mr-1 h-4 w-4" />
      {run.isPending ? "재개 중…" : "멈췄으면 이어서 진행"}
    </Button>
  );
}
