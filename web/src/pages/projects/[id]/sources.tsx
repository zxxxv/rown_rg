import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, ArrowRight, FileText, Loader2, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { decideSourcePool, parseSourcePoolPayload, useDecideCollectMore } from "@/api/checkpoints";
import { ApiError } from "@/api/client";
import { progressKeys, useProgressSnapshot } from "@/api/progress";
import {
  useDeleteSource,
  usePatchSource,
  useProjectSources,
  useUploadProjectSource,
} from "@/api/sources";
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
  const queryClient = useQueryClient();
  const { user, logout } = useAuth();

  // 검토는 SOURCE_POOL 게이트가 열려 있을 때만 유효 - 확정 후(작성·완료)에는
  // 이 페이지가 읽기 전용 기록이 된다(채택/제외/추가 검색/검토 완료 숨김).
  // 게이트가 열리기 전(초기 수집 중)에는 스냅샷을 폴링해 게이트 도착을 따라잡는다.
  const snapshot = useProgressSnapshot(projectId, true, { refetchInterval: 7_000 });
  const reviewOpen = snapshot.data?.pending_gate?.gate === "source_pool";
  // 초기 수집 진행 중 - 게이트 전이라 검토는 아직 못 하지만, 상태는 알려야 한다.
  const gathering = snapshot.data?.status === "researching" && !reviewOpen;
  const notStarted = (snapshot.data?.status ?? "created") === "created";
  // 자료 삭제는 백엔드가 시작 전·수집 중에만 허용한다(색인 이후엔 청크 정합 때문).
  const canDelete = notStarted || snapshot.data?.status === "researching";
  // 수집 목표(권장 하한) - 게이트 전에도 진행 배너에 "현재 n / 목표 m"으로 보여준다.
  const sourceTarget = snapshot.data?.source_target;

  // 추가 검색(+10건) - 게이트를 닫지 않는 보충 수집. 시작 시점 자료 수를 기준선으로
  // 잡아 배너에 "+n건 수집됨"을 보여주고, 도는 동안 목록을 폴링으로 따라잡는다.
  const collectMore = useDecideCollectMore();
  const [collectBaseline, setCollectBaseline] = useState<number | null>(null);
  const collecting = collectBaseline !== null;
  const sourcesQuery = useProjectSources(projectId, {
    refetchInterval: collecting || gathering ? 5_000 : false,
  });
  const patchSource = usePatchSource(projectId);

  // 커버리지 신호(게이트 payload) - 총량 부족·매칭 0건 절을 검토 화면에 표면화한다.
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

  // 필터 사이드바 제거됨 - 실데이터에 의미 있는 분류축이 없어(전부 웹 수집)
  // 분류가 실제로 동작하지 않았다. 목록은 수집 순서 그대로.
  const items = sourcesQuery.data?.items ?? [];
  // 파일 자료(업로드·라이브러리)는 카드 목록이 아니라 업로드 공간 아래 나열
  // (2026-08-08 사용자 결정) - 카드 목록은 웹 수집 자료 전용이 된다.
  const fileItems = useMemo(() => items.filter((s) => s.source_kind !== "web_search"), [items]);
  const webItems = useMemo(() => items.filter((s) => s.source_kind === "web_search"), [items]);

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

  // 채택/제외 2-상태(기본 채택) - '대기' 상태는 실계약에 없다
  const counts = useMemo(() => {
    let included = 0;
    let excluded = 0;
    for (const s of items) {
      if (s.is_included === false) excluded++;
      else included++;
    }
    return { included, excluded };
  }, [items]);

  // 직접 업로드 → 저장 + 즉시 색인(백엔드). 진행률은 XHR 전송 바이트 실측 -
  // 전송이 끝나면 "저장 중…"으로 바뀌고, 색인은 목록 행의 상태가 이어받는다.
  const handleFiles = (files: File[]) => {
    for (const file of files) {
      const rowId = `${file.name}-${file.size}-${file.lastModified}`;
      setUploading((u) =>
        u.some((f) => f.id === rowId) ? u : [...u, { id: rowId, name: file.name, progress: 0 }],
      );
      const onProgress = (percent: number) =>
        setUploading((u) => u.map((f) => (f.id === rowId ? { ...f, progress: percent } : f)));
      uploadSource.mutate(
        { file, onProgress },
        {
          onSuccess: () => {
            setUploading((u) => u.filter((f) => f.id !== rowId));
            toast.success(`"${file.name}" 업로드·색인 완료`);
          },
          onError: (err: unknown) => {
            setUploading((u) => u.filter((f) => f.id !== rowId));
            const msg = err instanceof ApiError ? err.message : "업로드에 실패했습니다.";
            toast.error("업로드 실패", { description: msg });
          },
        },
      );
    }
  };

  // 파일 자료 삭제 - 마음이 바뀐 업로드/불러오기 자료를 색인 청크째 제거.
  // 작성 시작 후엔 백엔드가 잠근다(인용이 청크를 참조) - 그땐 제외 토글의 몫.
  const deleteSource = useDeleteSource(projectId);
  const handleDelete = (s: Source) => {
    if (deleteSource.isPending) return;
    if (!window.confirm(`"${s.title}"을(를) 삭제할까요? 색인된 조각도 함께 제거됩니다.`)) return;
    deleteSource.mutate(s.id, {
      onSuccess: () => toast.success(`"${s.title}" 삭제 완료`),
      onError: (err: unknown) => {
        const msg = err instanceof ApiError ? err.message : "삭제에 실패했습니다.";
        toast.error("삭제 실패", { description: msg });
      },
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
      // 승인 직후 스냅샷 캐시를 즉시 무효화 - 7초 폴링을 기다리면 이 화면의
      // 검토 완료 바와 개요 CTA가 승인 후에도 잠깐 남아 "안 눌린 것처럼" 보인다
      // (2026-08-07 실사용 보고). 게이트 해소는 백엔드에서 이미 끝난 상태다.
      void queryClient.invalidateQueries({ queryKey: progressKeys.snapshot(projectId) });
      toast.success("자료 검토 완료 - 색인을 시작합니다.");
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
            {notStarted
              ? "아직 자료 조사를 시작하지 않았습니다 - 준비한 파일을 미리 올려두면 수집 결과와 함께 검토·색인됩니다."
              : gathering
                ? "AI가 자료를 검색하고 있습니다 - 수집이 끝나면 여기서 검토를 시작할 수 있습니다. 그 전에도 파일을 올려둘 수 있습니다."
                : reviewOpen
                  ? "AI가 수집한 자료를 검토하고 채택할 자료를 결정해 주세요. 추가 자료는 아래 드롭존에 끌어다 놓으면 됩니다."
                  : "검토가 완료된 자료 목록입니다 (읽기 전용) - 채택된 자료만 본문 작성의 검색 근거로 사용됐습니다."}
          </p>
        </header>

        {/* 자료 추가는 항상 열어둔다 - 업로드·라이브러리 불러오기는 즉시 색인되므로
            파이프라인 단계와 무관하게 동작한다(백엔드도 상태 가드가 없다). */}
        <section className="flex flex-col gap-3 rounded-lg border border-border bg-bg-secondary/40 p-4">
          <div>
            <h2 className="text-sm font-semibold text-fg">자료 추가</h2>
            <p className="text-xs text-fg-tertiary">
              라이브러리에서 체크로 골라 추가하거나, 오른쪽에 파일을 끌어다 놓으세요. 인터넷 추가
              검색은 하단에 있습니다. 불러오기·업로드는 PDF·HWPX·DOCX·MD·TXT가 색인됩니다.
            </p>
          </div>
          {/* 좌: 라이브러리 트리(체크 다중 선택) · 우: 업로드 + 추가된 파일 나열
              - 파일 자료는 하단 카드가 아니라 여기 나열된다(2026-08-08 사용자 결정) */}
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <LibraryTreePanel projectId={projectId} />
            <div className="flex flex-col gap-2">
              <UploadDropzone onFiles={handleFiles} uploading={uploading} />
              <FileSourceRows items={fileItems} onDelete={canDelete ? handleDelete : undefined} />
            </div>
          </div>
        </section>

        {gathering ? (
          <div className="flex items-center gap-3 rounded-md border border-fg-info/30 bg-bg-info px-4 py-3 text-sm">
            <Loader2 className="h-4 w-4 shrink-0 animate-spin text-fg-info" aria-hidden />
            <p className="text-fg-secondary">
              자료 검색이 진행 중입니다 - 수집되는 대로 목록에 추가되고, 끝나면 검토 지점이
              열립니다.
              {/* 목표는 권장 하한(백엔드 research_min_sources) - 미달이어도 차단하지 않고
                  검토 지점에서 '추가 조사'로 보충한다. */}
              <span className="ml-1 font-medium text-fg">
                현재 {items.length}건{sourceTarget ? ` / 목표 ${sourceTarget}건` : ""}
              </span>
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

        {/* 커버리지 경고 - 총량 부족·매칭 0건 절. 차단이 아니라 '추가 검색' 판단 근거. */}
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
          ) : webItems.length === 0 ? (
            <p className="rounded border border-dashed border-border bg-bg-secondary p-4 text-sm text-fg-tertiary">
              웹 수집 자료가 없습니다 - 추가된 파일 자료는 위 목록에 있습니다.
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {webItems.map((s) => (
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
        <div className="flex items-center gap-3">
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
                    {isPending ? "처리 중…" : `이 자료로 시작 (${includedCount}개 채택)`}
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

/** 파일 자료(업로드·라이브러리) 컴팩트 나열 - 카드 목록 대신 업로드 공간 아래.

onDelete가 있으면(검토 중) 삭제 버튼을 붙인다 - 삭제는 색인 청크까지 제거되며,
작성 시작 후엔 백엔드가 거부한다. 검토 종료 후엔 읽기 전용 나열. */
function FileSourceRows({ items, onDelete }: { items: Source[]; onDelete?: (s: Source) => void }) {
  if (items.length === 0) return null;
  return (
    <ul className="flex flex-col gap-1">
      {items.map((s) => (
        <li
          key={s.id}
          className="flex items-center gap-2 rounded border border-border bg-bg px-2.5 py-1.5"
        >
          <FileText className="h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
          <span className="flex-1 truncate text-sm text-fg">{s.title}</span>
          <Badge variant="outline" className="shrink-0 text-xs font-normal">
            {s.source_kind === "library" ? "라이브러리" : "업로드"}
          </Badge>
          {onDelete ? (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 shrink-0"
              onClick={() => onDelete(s)}
              aria-label={`${s.title} 삭제`}
            >
              <Trash2 className="h-4 w-4 text-fg-danger" aria-hidden />
            </Button>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
