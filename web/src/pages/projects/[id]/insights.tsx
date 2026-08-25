import { ArrowLeft, FileText, Loader2, RefreshCw } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { useInsights, useRebuildInsights } from "@/api/insights";
import { useProject } from "@/api/projects";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { MarkdownContent } from "@/features/preview/MarkdownContent";
import { useAuth } from "@/hooks/useAuth";

/** 시사점 요약 - 본문의 시사점·제언 절을 2~3쪽으로 압축한 별도 산출물.
 *
 * 원본 보고서는 건드리지 않는다. 이 요약은 한글(HWPX) 파일에 실리지 않고 여기서만
 * 본다(2026-08-25 결정) - 화면이 그 사실을 명시해야 사용자가 다운로드 파일에서
 * 찾다가 없다고 오해하지 않는다.
 */
export default function InsightsPage() {
  const { id: projectId = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const projectQuery = useProject(projectId);
  const insightsQuery = useInsights(projectId);
  const rebuild = useRebuildInsights(projectId);

  const insights = insightsQuery.data;
  const running = insights?.running ?? false;
  const isCompleted = projectQuery.data?.status === "completed";

  const onRebuild = () => {
    rebuild.mutate(undefined, {
      onSuccess: () => toast.success("시사점 요약을 다시 만들고 있습니다"),
      onError: () => toast.error("다시 만들기를 시작하지 못했습니다"),
    });
  };

  return (
    <AppShell
      user={user ? { name: user.name, role: user.role } : null}
      onLogout={() => void logout()}
    >
      <Button
        variant="ghost"
        size="sm"
        className="mb-3 w-fit text-fg-secondary"
        onClick={() => navigate(`/projects/${projectId}/overview`)}
      >
        <ArrowLeft className="mr-1 h-4 w-4" />
        프로젝트 개요
      </Button>

      {/* 읽는 문서라 폭을 묶는다 - 전폭(1440px)이면 한 줄이 너무 길어 눈이 줄을
          놓친다. 본문 카드와 머리글의 좌우가 같은 선에 오도록 같은 폭을 쓴다. */}
      <header className="mx-auto mb-4 flex w-full max-w-4xl flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-lg font-semibold text-fg">시사점 요약</h1>
            <Badge variant="outline">한글 파일 미포함</Badge>
          </div>
          <p className="text-sm text-fg-secondary">
            본문의 시사점·제언 절을 2~3쪽으로 압축한 별도 산출물입니다. 원본 보고서는 그대로이며,
            이 요약은 HWPX 다운로드에 들어가지 않습니다.
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={onRebuild}
          disabled={running || rebuild.isPending || !isCompleted}
          title={
            isCompleted
              ? "지금 저장된 본문으로 요약을 다시 만듭니다"
              : "본문이 완성된 뒤에 만들 수 있습니다"
          }
        >
          {running || rebuild.isPending ? (
            <Loader2 className="mr-1 h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="mr-1 h-4 w-4" />
          )}
          {running ? "만드는 중…" : "다시 만들기"}
        </Button>
      </header>

      {insightsQuery.isLoading ? (
        <LoadingSkeleton />
      ) : insightsQuery.isError ? (
        <EmptyState
          title="시사점 요약을 불러올 수 없습니다"
          description="잠시 후 다시 시도해 주세요."
          action={
            <Button variant="outline" onClick={() => void insightsQuery.refetch()}>
              다시 시도
            </Button>
          }
        />
      ) : insights?.content ? (
        <article className="mx-auto w-full max-w-4xl rounded border border-border bg-bg">
          <div className="px-6 py-4">
            <MarkdownContent content={insights.content} />
          </div>
          {insights.source_sections.length > 0 ? (
            <footer className="flex flex-wrap items-center gap-1.5 border-t border-border px-4 py-3 text-xs text-fg-secondary">
              <FileText className="h-3.5 w-3.5" aria-hidden />
              <span>요약한 절:</span>
              {insights.source_sections.map((label) => (
                <button
                  key={label}
                  type="button"
                  className="rounded border border-border px-1.5 py-0.5 hover:bg-bg-secondary"
                  onClick={() => navigate(`/projects/${projectId}/preview`)}
                >
                  {label}
                </button>
              ))}
            </footer>
          ) : null}
        </article>
      ) : running ? (
        <EmptyState
          title="시사점 요약을 만들고 있습니다"
          description="본문 분량에 따라 30초~2분쯤 걸립니다. 다 되면 이 화면이 저절로 바뀝니다."
        />
      ) : (
        <EmptyState
          title="아직 시사점 요약이 없습니다"
          description={
            isCompleted
              ? "'다시 만들기'를 누르면 지금 저장된 본문으로 요약을 만듭니다."
              : "본문이 완성되면 조립 단계에서 자동으로 만들어집니다."
          }
          action={
            isCompleted ? (
              <Button onClick={onRebuild} disabled={rebuild.isPending}>
                <RefreshCw className="mr-1 h-4 w-4" />
                지금 만들기
              </Button>
            ) : undefined
          }
        />
      )}
    </AppShell>
  );
}
