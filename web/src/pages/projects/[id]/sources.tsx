import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, ArrowRight, Loader2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { decideSourcePool, parseSourcePoolPayload, useDecideCollectMore } from "@/api/checkpoints";
import { ApiError } from "@/api/client";
import { useProgressSnapshot } from "@/api/progress";
import { usePatchSource, useProjectSources, useUploadProjectSource } from "@/api/sources";
import type { Source } from "@/api/types";
import { SourceCard } from "@/components/data-display/SourceCard";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { LibraryTreePanel } from "@/features/source-review/LibraryTreePanel";
import { SourceDetailDialog } from "@/features/source-review/SourceDetailDialog";
import { UploadDropzone, type UploadingFile } from "@/features/source-review/UploadDropzone";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";

export default function SourcesPage() {
  const { id: projectId = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { user, logout } = useAuth();

  // 검토는 SOURCE_POOL 게이트가 열려 있을 때만 유효 — 확정 후(작성·완료)에는
  // 이 페이지가 읽기 전용 기록이 된다(채택/제외/추가 검색/검토 완료 숨김).
  // 게이트가 열리기 전(초기 수집 중)에는 스냅샷을 폴링해 게이트 도착을 따라잡는다.
  const snapshot = useProgressSnapshot(projectId, true, { refetchInterval: 7_000 });
  const reviewOpen = snapshot.data?.pending_gate?.gate === "source_pool";
  // 초기 수집 진행 중 — 게이트 전이라 검토는 아직 못 하지만, 상태는 알려야 한다.
  const gathering = snapshot.data?.status === "researching" && !reviewOpen;

  // 추가 검색(+10건) — 게이트를 닫지 않는 보충 수집. 시작 시점 자료 수를 기준선으로
  // 잡아 배너에 "+n건 수집됨"을 보여주고, 도는 동안 목록을 폴링으로 따라잡는다.
  const collectMore = useDecideCollectMore();
  const [collectBaseline, setCollectBaseline] = useState<number | null>(null);
  const collecting = collectBaseline !== null;
  const sourcesQuery = useProjectSources(projectId, {
    refetchInterval: collecting || gathering ? 5_000 : false,
  });
  const patchSource = usePatchSource(projectId);

  // 커버리지 신호(게이트 payload) — 총량 부족·매칭 0건 절을 검토 화면에 표면화한다.
  const coverage = useMemo(() => {
    if (!reviewOpen) return null;
    return parseSourcePoolPayload(snapshot.data?.pending_gate?.payload)?.coverage ?? null;
  }, [reviewOpen, snapshot.data]);

  const handleCollectMore = () => {
    if (collectMore.isPending || collecting) return;
    const baseline = sourcesQuery.data?.items.length ?? 0;
    collectMore.mutate(projectId, {
      onSuccess: () => {
        setCollectBaseline(baseline);
        toast.success("추가 검색을 시작했습니다 (+10건 목표)", {
          description: "기존 자료는 유지됩니다 - 수집되는 대로 목록에 추가됩니다.",
        });
      },
      onError: (err: unknown) => {
        const msg = err instanceof ApiError ? err.message : "추가 검색 요청에 실패했습니다.";
        toast.error("추가 검색 실패", {
          description:
            msg.includes("게이트") || msg.includes("대기")
              ? "자료 검토 대기 상태가 아닙니다 - 개요의 진행 단계를 확인하세요."
              : msg,
        });
      },
    });
  };

  const [activeSource, setActiveSource] = useState<Source | null>(null);
  const [uploading, setUploading] = useState<UploadingFile[]>([]);
  const uploadSource = useUploadProjectSource(projectId);

  // 필터 사이드바 제거됨 — 실데이터에 의미 있는 분류축이 없어(전부 웹 수집)
  // 분류가 실제로 동작하지 않았다. 목록은 수집 순서 그대로.
  const items = sourcesQuery.data?.items ?? [];

  // 백그라운드 수집 종료 판정: 배치 목표(+10건) 도달 시 즉시, 아니면 4분 타임아웃.
  // (수집 완료를 직접 알리는 신호가 이 페이지엔 없어 보수적으로 마감한다)
  const newCount = collecting ? Math.max(0, items.length - (collectBaseline ?? 0)) : 0;
  useEffect(() => {
    if (!collecting) return;
    if (newCount >= 10) {
      setCollectBaseline(null);
      toast.success(`추가 검색 완료 - 새 자료 ${newCount}건이 도착했습니다.`);
      return;
    }
    const timer = setTimeout(() => setCollectBaseline(null), 4 * 60_000);
    return () => clearTimeout(timer);
  }, [collecting, newCount]);

  // 채택/제외 2-상태(기본 채택) — '대기' 상태는 실계약에 없다
  const counts = useMemo(() => {
    let included = 0;
    let excluded = 0;
    for (const s of items) {
      if (s.is_included === false) excluded++;
      else included++;
    }
    return { included, excluded };
  }, [items]);

  // 직접 업로드 → 저장 + 즉시 색인(백엔드). 진행 표시는 근사(전송·색인 단일 단계).
  // PDF·HWPX만 색인되며, 그 외 형식은 백엔드가 명확한 오류로 알려준다.
  const handleFiles = (files: File[]) => {
    for (const file of files) {
      const rowId = `${file.name}-${file.size}-${file.lastModified}`;
      setUploading((u) =>
        u.some((f) => f.id === rowId) ? u : [...u, { id: rowId, name: file.name, progress: 50 }],
      );
      uploadSource.mutate(file, {
        onSuccess: () => {
          setUploading((u) => u.filter((f) => f.id !== rowId));
          toast.success(`"${file.name}" 업로드·색인 완료`);
        },
        onError: (err: unknown) => {
          setUploading((u) => u.filter((f) => f.id !== rowId));
          const msg = err instanceof ApiError ? err.message : "업로드에 실패했습니다.";
          toast.error("업로드 실패", { description: msg });
        },
      });
    }
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
      toast.success("자료 검토 완료 - 작성을 이어갑니다.");
      navigate(`/projects/${projectId}/overview`);
    },
    onError: (err: unknown) => {
      const msg = err instanceof ApiError ? err.message : "검토 완료 처리에 실패했습니다.";
      toast.error("검토 완료 실패", {
        description:
          msg.includes("게이트") || msg.includes("대기")
            ? "자료 검토 대기 상태가 아닙니다 - 개요의 진행 단계를 확인하세요."
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
            <div className="flex items-center gap-1.5">
              <Badge variant="outline" className="px-2.5 py-1 font-mono text-sm">
                총 {items.length}
              </Badge>
              <Badge className="border-fg-success/30 bg-bg-success px-2.5 py-1 font-mono text-sm text-fg-success">
                채택 {counts.included}
              </Badge>
              <Badge className="border-fg-danger/30 bg-bg-danger px-2.5 py-1 font-mono text-sm text-fg-danger">
                제외 {counts.excluded}
              </Badge>
            </div>
          </div>
          <p className="text-sm text-fg-secondary">
            {gathering
              ? "AI가 자료를 검색하고 있습니다 - 수집이 끝나면 여기서 검토를 시작할 수 있습니다."
              : reviewOpen
                ? "AI가 수집한 자료를 검토하고 채택할 자료를 결정해 주세요. 추가 자료는 아래 드롭존에 끌어다 놓으면 됩니다."
                : "검토가 완료된 자료 목록입니다 (읽기 전용) - 채택된 자료만 본문 작성의 검색 근거로 사용됐습니다."}
          </p>
        </header>

        {reviewOpen ? (
          <section className="flex flex-col gap-3 rounded-lg border border-border bg-bg-secondary/40 p-4">
            <div>
              <h2 className="text-sm font-semibold text-fg">자료 추가</h2>
              <p className="text-xs text-fg-tertiary">
                라이브러리에서 체크로 골라 추가하거나, 오른쪽에 파일을 끌어다 놓으세요. 인터넷 추가
                검색은 하단에 있습니다. 불러오기·업로드는 PDF·HWPX·DOCX·MD·TXT가 색인됩니다.
              </p>
            </div>
            {/* 좌: 라이브러리 트리(체크 다중 선택) · 우: 업로드 — 2026-08-08 사용자 결정 배치 */}
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <LibraryTreePanel projectId={projectId} />
              <UploadDropzone onFiles={handleFiles} uploading={uploading} />
            </div>
          </section>
        ) : null}

        {gathering ? (
          <div className="flex items-center gap-3 rounded-md border border-fg-info/30 bg-bg-info px-4 py-3 text-sm">
            <Loader2 className="h-4 w-4 shrink-0 animate-spin text-fg-info" aria-hidden />
            <p className="text-fg-secondary">
              자료 검색이 진행 중입니다 - 수집되는 대로 목록에 추가되고, 끝나면 검토 지점이
              열립니다.
              {items.length > 0 ? (
                <span className="ml-1 font-medium text-fg">현재 {items.length}건</span>
              ) : null}
            </p>
          </div>
        ) : null}

        {collecting ? (
          <div className="flex items-center gap-3 rounded-md border border-fg-info/30 bg-bg-info px-4 py-3 text-sm">
            <Loader2 className="h-4 w-4 shrink-0 animate-spin text-fg-info" aria-hidden />
            <p className="text-fg-secondary">
              추가 검색이 백그라운드에서 진행 중입니다 - 새 자료가 도착하는 대로 목록에 추가됩니다.
              {newCount > 0 ? (
                <span className="ml-1 font-medium text-fg">+{newCount}건 수집됨</span>
              ) : null}
            </p>
          </div>
        ) : null}

        {/* 커버리지 경고 — 총량 부족·매칭 0건 절. 차단이 아니라 '추가 검색' 판단 근거. */}
        {coverage && (!coverage.sufficient || coverage.uncovered_sections.length > 0) ? (
          <div className="flex flex-col gap-1.5 rounded-md border border-fg-warning/40 bg-bg-warning px-4 py-3 text-sm">
            {!coverage.sufficient ? (
              <p className="flex items-center gap-2 text-fg">
                <AlertTriangle className="h-4 w-4 shrink-0 text-fg-warning" aria-hidden />
                본문 있는 자료가 부족합니다 ({coverage.n_sources}/{coverage.min_required}건) - 추가
                검색을 권장합니다.
              </p>
            ) : null}
            {coverage.uncovered_sections.length > 0 ? (
              <p className="text-xs text-fg-secondary">
                매칭 자료가 없는 절 {coverage.uncovered_sections.length}개:{" "}
                <span className="font-medium text-fg">
                  {coverage.uncovered_sections.slice(0, 6).join(" · ")}
                  {coverage.uncovered_sections.length > 6 ? " 외" : ""}
                </span>{" "}
                - 이 절들은 근거 부족으로 빈약하게 작성될 수 있습니다.
              </p>
            ) : null}
          </div>
        ) : null}

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
              title={gathering ? "자료를 검색하고 있습니다" : "아직 수집된 자료가 없습니다"}
              description={
                gathering
                  ? "AI가 목차 기반으로 자료를 수집 중입니다 - 도착하는 대로 자동 표시됩니다."
                  : "AI 자동 검색이 진행되거나 파일을 직접 업로드하면 표시됩니다."
              }
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
                  sections={s.matched_sections}
                  kindLabel={s.source_kind === "web_search" ? "웹 검색" : undefined}
                  onClick={() => setActiveSource(s)}
                  className={cn(
                    s.is_included === true && "border-fg-success/40 bg-bg-success",
                    s.is_included === false && "opacity-60",
                  )}
                  actions={
                    reviewOpen ? (
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
                    ) : (
                      <>
                        {/* 수집 중(게이트 전)엔 채택/제외가 아직 결정 대상이 아니라 배지를 숨긴다 */}
                        {!gathering ? (
                          <Badge
                            variant="outline"
                            className={
                              s.is_included === false
                                ? "border-fg-danger/30 text-fg-danger"
                                : "border-fg-success/30 text-fg-success"
                            }
                          >
                            {s.is_included === false ? "제외됨" : "채택됨"}
                          </Badge>
                        ) : null}
                        <Button size="sm" variant="ghost" onClick={() => setActiveSource(s)}>
                          상세보기
                        </Button>
                      </>
                    )
                  }
                />
              ))}
            </div>
          )}
        </main>
      </div>

      {reviewOpen ? (
        <FinalizeBar
          canFinalize={canFinalize}
          isPending={handleFinalize.isPending}
          onUploadFocus={() => window.scrollTo({ top: 0, behavior: "smooth" })}
          onFinalize={() => handleFinalize.mutate()}
          includedCount={counts.included}
          onCollectMore={handleCollectMore}
          collectPending={collectMore.isPending || collecting}
        />
      ) : null}

      <SourceDetailDialog
        source={activeSource}
        open={Boolean(activeSource)}
        onOpenChange={(o) => {
          if (!o) setActiveSource(null);
        }}
        readOnly={!reviewOpen}
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
  onCollectMore,
  collectPending,
}: {
  canFinalize: boolean;
  isPending: boolean;
  includedCount: number;
  onUploadFocus: () => void;
  onFinalize: () => void;
  onCollectMore: () => void;
  collectPending: boolean;
}) {
  return (
    <div className="fixed inset-x-0 bottom-0 z-10 border-t border-border bg-bg/95 backdrop-blur">
      <div className="mx-auto flex max-w-screen-2xl items-center justify-between gap-3 px-8 py-3">
        <Button variant="ghost" onClick={onUploadFocus}>
          <ArrowLeft className="mr-1 h-4 w-4" />
          자료 추가 업로드
        </Button>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="lg"
            disabled={collectPending || isPending}
            onClick={onCollectMore}
          >
            {collectPending ? "추가 검색 진행 중…" : "추가 검색 (+10건)"}
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
    </div>
  );
}
