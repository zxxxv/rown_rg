import { useMutation } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { decideSourcePool } from "@/api/checkpoints";
import { ApiError } from "@/api/client";
import { usePatchSource, useProjectSources } from "@/api/sources";
import type { Source } from "@/api/types";
import { SourceCard } from "@/components/data-display/SourceCard";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { SourceDetailDialog } from "@/features/source-review/SourceDetailDialog";
import { UploadDropzone, type UploadingFile } from "@/features/source-review/UploadDropzone";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";

export default function SourcesPage() {
  const { id: projectId = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { user, logout } = useAuth();

  const sourcesQuery = useProjectSources(projectId);
  const patchSource = usePatchSource(projectId);

  const [activeSource, setActiveSource] = useState<Source | null>(null);
  const [uploading] = useState<UploadingFile[]>([]);

  // 필터 사이드바 제거됨 — 실데이터에 의미 있는 분류축이 없어(전부 웹 수집)
  // 분류가 실제로 동작하지 않았다. 목록은 수집 순서 그대로.
  const items = sourcesQuery.data?.items ?? [];
  const counts = useMemo(() => {
    let included = 0;
    let excluded = 0;
    let pending = 0;
    for (const s of items) {
      if (s.is_included === true) included++;
      else if (s.is_included === false) excluded++;
      else pending++;
    }
    return { included, excluded, pending };
  }, [items]);

  // 업로드 → 인덱싱 합류는 라이브러리 연동과 함께 배선 예정(백엔드 미구현).
  const handleFiles = (_files: File[]) => {
    toast.info("자료 업로드는 준비 중입니다", {
      description: "라이브러리 연동 후 활성화됩니다 — 지금은 웹 수집 자료만 검토할 수 있습니다.",
    });
  };

  const setIncluded = (sid: string, is_included: boolean) => {
    patchSource.mutate(
      { sid, is_included },
      {
        onError: (err: unknown) => {
          const msg = err instanceof ApiError ? err.message : "자료 상태 변경에 실패했습니다.";
          toast.error("롤백됨", { description: msg });
        },
      },
    );
  };

  // 확정 = 진행 게이트의 decide(approve). 제외 선택은 PATCH로 이미 반영돼 있어
  // 빈 excluded로 승인만 보낸다. 게이트 대기 상태가 아니면 백엔드가 422로 알려준다.
  const handleFinalize = useMutation({
    mutationFn: () => decideSourcePool({ projectId, excludedSourceIds: [], action: "approve" }),
    onSuccess: () => {
      toast.success("자료 검토 완료 — 작성을 이어갑니다.");
      navigate(`/projects/${projectId}/progress`);
    },
    onError: (err: unknown) => {
      const msg = err instanceof ApiError ? err.message : "검토 완료 처리에 실패했습니다.";
      toast.error("검토 완료 실패", {
        description:
          msg.includes("게이트") || msg.includes("대기")
            ? "자료 검토 대기 상태가 아닙니다 — 진행 화면을 확인하세요."
            : msg,
      });
    },
  });

  const canFinalize = counts.included > 0 && !handleFinalize.isPending;

  return (
    <AppShell
      user={user ? { name: user.name, role: user.role } : null}
      onLogout={() => void logout()}
      tokenUsage={{ used: 1_240_000, limit: 5_000_000 }}
    >
      <div className="flex flex-col gap-6 pb-24">
        <header className="flex flex-col gap-3">
          <Button
            variant="ghost"
            size="sm"
            className="w-fit text-fg-secondary"
            onClick={() => navigate(`/projects/${projectId}/overview`)}
          >
            <ArrowLeft className="mr-1 h-4 w-4" />
            프로젝트 개요
          </Button>
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-2">
              <h1 className="text-3xl font-semibold text-fg">자료 검토</h1>
              <Badge variant="secondary" className="font-mono">
                검토 지점 #1
              </Badge>
            </div>
            <div className="flex items-center gap-3 font-mono text-sm">
              <span className="text-fg-success">채택 {counts.included}</span>
              <span className="text-fg-tertiary">대기 {counts.pending}</span>
              <span className="text-fg-danger">제외 {counts.excluded}</span>
            </div>
          </div>
          <p className="text-sm text-fg-secondary">
            AI가 수집한 자료를 검토하고 채택할 자료를 결정해 주세요. 추가 자료는 아래 드롭존에
            끌어다 놓으면 됩니다.
          </p>
        </header>

        <UploadDropzone onFiles={handleFiles} uploading={uploading} />

        <main>
          {sourcesQuery.isLoading ? (
            <LoadingSkeleton variant="card" count={6} />
          ) : sourcesQuery.isError ? (
            <EmptyState
              title="자료를 불러오지 못했습니다"
              description="잠시 후 다시 시도해 주세요."
              action={
                <Button variant="outline" onClick={() => void sourcesQuery.refetch()}>
                  다시 시도
                </Button>
              }
            />
          ) : items.length === 0 ? (
            <EmptyState
              title="아직 수집된 자료가 없습니다"
              description="AI 자동 검색이 진행되거나 파일을 직접 업로드하면 표시됩니다."
            />
          ) : (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {items.map((s) => (
                <SourceCard
                  key={s.id}
                  title={s.title}
                  source={s.source}
                  publishedAt={s.published_at}
                  pages={s.pages}
                  reliability={s.reliability}
                  summary={s.summary}
                  kindLabel={s.source_kind === "web_search" ? "웹 검색" : undefined}
                  onClick={() => setActiveSource(s)}
                  className={cn(
                    s.is_included === true && "border-fg-success/40",
                    s.is_included === false && "opacity-60",
                  )}
                  actions={
                    <>
                      <Button
                        size="sm"
                        variant={s.is_included ? "secondary" : "default"}
                        disabled={s.is_included === true}
                        onClick={() => setIncluded(s.id, true)}
                      >
                        {s.is_included ? "채택됨" : "채택"}
                      </Button>
                      <Button
                        size="sm"
                        variant={s.is_included === false ? "secondary" : "outline"}
                        disabled={s.is_included === false}
                        onClick={() => setIncluded(s.id, false)}
                      >
                        {s.is_included === false ? "제외됨" : "제외"}
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setActiveSource(s)}>
                        상세보기
                      </Button>
                    </>
                  }
                />
              ))}
            </div>
          )}
        </main>
      </div>

      <FinalizeBar
        canFinalize={canFinalize}
        isPending={handleFinalize.isPending}
        onUploadFocus={() => window.scrollTo({ top: 0, behavior: "smooth" })}
        onFinalize={() => handleFinalize.mutate()}
        includedCount={counts.included}
      />

      <SourceDetailDialog
        source={activeSource}
        open={Boolean(activeSource)}
        onOpenChange={(o) => {
          if (!o) setActiveSource(null);
        }}
        onInclude={(sid) => setIncluded(sid, true)}
        onExclude={(sid) => setIncluded(sid, false)}
      />
    </AppShell>
  );
}

function FinalizeBar({
  canFinalize,
  isPending,
  includedCount,
  onUploadFocus,
  onFinalize,
}: {
  canFinalize: boolean;
  isPending: boolean;
  includedCount: number;
  onUploadFocus: () => void;
  onFinalize: () => void;
}) {
  return (
    <div className="fixed inset-x-0 bottom-0 z-10 border-t border-border bg-bg/95 backdrop-blur">
      <div className="mx-auto flex max-w-screen-2xl items-center justify-between gap-3 px-8 py-3">
        <Button variant="ghost" onClick={onUploadFocus}>
          <ArrowLeft className="mr-1 h-4 w-4" />
          자료 추가 업로드
        </Button>
        <TooltipProvider delayDuration={150}>
          <Tooltip>
            <TooltipTrigger asChild>
              <span>
                <Button size="lg" disabled={!canFinalize} onClick={onFinalize}>
                  {isPending ? "처리 중…" : `검토 완료 (${includedCount}개 채택)`}
                  <ArrowRight className="ml-1 h-4 w-4" />
                </Button>
              </span>
            </TooltipTrigger>
            {!canFinalize && includedCount === 0 ? (
              <TooltipContent>최소 1개 자료를 채택하세요</TooltipContent>
            ) : null}
          </Tooltip>
        </TooltipProvider>
      </div>
    </div>
  );
}
