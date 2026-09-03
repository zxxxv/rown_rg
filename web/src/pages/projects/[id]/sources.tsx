import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, ArrowRight, FileText, Loader2, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { decideSourcePool, parseSourcePoolPayload } from "@/api/checkpoints";
import { ApiError } from "@/api/client";
import { progressKeys, useProgressSnapshot } from "@/api/progress";
import {
  fetchSourceImpact,
  useCollectMore,
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
import { ExcludeImpactDialog } from "@/features/source-review/ExcludeImpactDialog";
import { LibraryTreePanel } from "@/features/source-review/LibraryTreePanel";
import { SourceDetailDialog } from "@/features/source-review/SourceDetailDialog";
import { UploadDropzone, type UploadingFile } from "@/features/source-review/UploadDropzone";
import { useAuth } from "@/hooks/useAuth";
import { formatSize } from "@/lib/format";
import { POLL_LIST_MS, POLL_PAGE_MS } from "@/lib/intervals";
import { cn } from "@/lib/utils";

export default function SourcesPage() {
  const { id: projectId = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { user, logout } = useAuth();

  // 검토는 SOURCE_POOL 게이트가 열려 있을 때만 유효 - 확정 후(작성·완료)에는
  // 이 페이지가 읽기 전용 기록이 된다(채택/제외/추가 검색/검토 완료 숨김).
  // 게이트가 열리기 전(초기 수집 중)에는 스냅샷을 폴링해 게이트 도착을 따라잡는다.
  const snapshot = useProgressSnapshot(projectId, true, { refetchInterval: POLL_PAGE_MS });
  const reviewOpen = snapshot.data?.pending_gate?.gate === "source_pool";
  // 채택/제외를 만질 수 있는가 - 검토 게이트가 열렸을 때뿐 아니라 **실행이 멈춰 있으면
  // 언제든** 허용한다. 보고서는 완주가 끝이 아니라 계속 손보는 대상이고, 완료 뒤 자료를
  // 빼려면 재개(reopen)밖에 길이 없던 것이 그 흐름을 막고 있었다(2026-08-26).
  // 실행 중에는 잠근다 - 작성이 쓰고 있는 자료 집합을 도중에 바꾸면 절마다 근거가 달라진다.
  const canCurate = reviewOpen || !(snapshot.data?.runner_alive ?? false);
  // 초기 수집 진행 중 - 게이트 전이라 검토는 아직 못 하지만, 상태는 알려야 한다.
  // **runner_alive를 함께 본다**: status는 이제 "어디까지 왔나"지 "지금 뭐가 도나"가
  // 아니다(2026-08-26 파생화). 단계만 보고 스피너를 걸면 멈춰 있는 프로젝트가 영원히
  // "수집 중"으로 남는다 - 이 화면이 바로 그 사고가 났던 자리다.
  const gathering =
    snapshot.data?.status === "researching" &&
    !reviewOpen &&
    (snapshot.data?.runner_alive ?? false);
  const notStarted = (snapshot.data?.status ?? "created") === "created";
  // 자료 삭제는 백엔드가 시작 전·수집 중에만 허용한다(색인 이후엔 청크 정합 때문).
  const canDelete = notStarted || snapshot.data?.status === "researching";
  // 수집 목표(권장 하한) - 게이트 전에도 진행 배너에 "현재 n / 목표 m"으로 보여준다.
  const sourceTarget = snapshot.data?.source_target;

  // 추가 검색(+10건) - 게이트를 닫지 않는 보충 수집. 시작 시점 자료 수를 기준선으로
  // 잡아 배너에 "+n건 수집됨"을 보여주고, 도는 동안 목록을 폴링으로 따라잡는다.
  const collectMore = useCollectMore(projectId);
  const [collectBaseline, setCollectBaseline] = useState<number | null>(null);
  const collecting = collectBaseline !== null;
  const sourcesQuery = useProjectSources(projectId, {
    refetchInterval: collecting || gathering ? POLL_LIST_MS : false,
  });
  const patchSource = usePatchSource(projectId);

  // 커버리지 신호(게이트 payload) - 총량 부족·매칭 0건 절을 검토 화면에 표면화한다.
  const coverage = useMemo(() => {
    if (!reviewOpen) return null;
    return parseSourcePoolPayload(snapshot.data?.pending_gate?.payload)?.coverage ?? null;
  }, [reviewOpen, snapshot.data]);

  // 검색 리허설 재개방 신호 - 색인 후 절마다 실제 검색을 돌려 본 근거 공백(실측).
  const rehearsal = useMemo(() => {
    if (!reviewOpen) return null;
    return parseSourcePoolPayload(snapshot.data?.pending_gate?.payload)?.rehearsal ?? null;
  }, [reviewOpen, snapshot.data]);

  // '다시 열기'로 열린 검토 - 웹 검색은 이미 끝났고 추가 검색은 선택이다.
  // 이 구분이 없으면 화면이 첫 수집 직후와 똑같이 말해 "검색이 더 돌아야 하나"로 읽힌다.
  const reopened = useMemo(() => {
    if (!reviewOpen) return false;
    return parseSourcePoolPayload(snapshot.data?.pending_gate?.payload)?.reopened ?? false;
  }, [reviewOpen, snapshot.data]);

  const handleCollectMore = () => {
    if (collectMore.isPending || collecting) return;
    const baseline = sourcesQuery.data?.items.length ?? 0;
    collectMore.mutate(undefined, {
      onSuccess: () => {
        setCollectBaseline(baseline);
        toast.success("추가 검색을 시작했습니다 (+10건 목표)", {
          description: "기존 자료는 유지됩니다 - 수집되는 대로 목록에 추가됩니다.",
        });
      },
      onError: (err: unknown) => {
        const msg = err instanceof ApiError ? err.message : "추가 검색 요청에 실패했습니다.";
        toast.error("추가 검색 실패", { description: msg });
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
    // 출처 경로별 건수 - 웹 수집이 몇 건이고 내가 올린 게 몇 건인지 한눈에 본다.
    // 총계만 있으면 "추가 검색이 실제로 자료를 늘렸나"를 화면에서 알 수 없었다.
    let web = 0;
    let files = 0;
    // 본문(조각)이 없는 파일 자료 - 올라갔는데 못 읽은 것. 총계에는 잡히지만 근거로는 못 쓴다.
    let emptyFiles = 0;
    for (const s of items) {
      if (s.is_included === false) excluded++;
      else included++;
      if (s.source_kind === "web_search") web++;
      else {
        files++;
        if (!s.indexing && !s.index_deferred && s.n_chunks === 0) emptyFiles++;
      }
    }
    return { included, excluded, web, files, emptyFiles };
  }, [items]);

  // 업로드는 두 단계다: ①전송(브라우저→서버, 진행률 실측) ②색인(서버, 파싱·임베딩).
  // 서버가 자리 행을 먼저 만들고 색인을 뒤에서 돌리므로, 행이 생긴 뒤에도 전송 목록에
  // 남겨 두면 같은 파일이 "저장 중"과 "색인 중"으로 두 번 보인다(2026-08-20 지적).
  // 행이 생기는 순간 아래 목록이 이어받게 하고 전송 목록에서는 뺀다.
  const transferring = useMemo(
    () => uploading.filter((u) => !items.some((s) => s.title === u.name)),
    [uploading, items],
  );

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

  // 제외를 누르면 **먼저 무엇이 걸려 있는지 확인한다**. 걸린 절이 없으면 창 없이 그냥
  // 뺀다 - 아무것도 안 걸린 제외까지 확인을 받으면 창이 소음이 되고, 소음이 된 확인창은
  // 읽지 않고 눌린다.
  const [excludeTarget, setExcludeTarget] = useState<Source | null>(null);
  const [checkingImpact, setCheckingImpact] = useState<string | null>(null);
  const requestExclude = async (s: Source) => {
    setCheckingImpact(s.id);
    try {
      const impact = await fetchSourceImpact(queryClient, projectId, s.id);
      if (impact.n_sections === 0) {
        setIncluded(s.id, false);
        return;
      }
      setExcludeTarget(s);
    } catch {
      // 영향을 못 재도 제외 자체는 사람의 권한이다 - 막지 않고 그대로 진행한다.
      setIncluded(s.id, false);
    } finally {
      setCheckingImpact(null);
    }
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
          {/* flex-wrap: 폰 폭에서 통계 배지 묶음이 제목을 짓누르면 한글이 세로로
              꺾인다(2026-08-20 폰 실측) - 배지 줄을 통째로 아랫줄로 내린다. */}
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
            <div className="flex items-center gap-2">
              <h1 className="whitespace-nowrap text-3xl font-semibold text-fg">자료 검토</h1>
              <Badge variant="secondary" className="font-mono">
                검토 지점 #1
              </Badge>
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge variant="outline" className="px-2.5 py-1 font-mono text-sm">
                총 {items.length}
              </Badge>
              <Badge variant="outline" className="px-2.5 py-1 font-mono text-sm">
                웹 {counts.web}
              </Badge>
              <Badge variant="outline" className="px-2.5 py-1 font-mono text-sm">
                업로드 {counts.files}
              </Badge>
              {counts.emptyFiles > 0 ? (
                <Badge className="border-fg-danger/30 bg-bg-danger px-2.5 py-1 font-mono text-sm text-fg-danger">
                  본문 없음 {counts.emptyFiles}
                </Badge>
              ) : null}
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
                : reopened
                  ? "보고서를 다시 열었습니다 - 웹 검색은 이미 끝났습니다. 파일을 올리거나 자료를 제외한 뒤 검토를 완료하면 색인부터 이어집니다. 인터넷 추가 검색은 선택입니다."
                  : reviewOpen
                    ? "AI가 수집한 자료를 검토하고 채택할 자료를 결정해 주세요. 추가 자료는 아래 드롭존에 끌어다 놓으면 됩니다."
                    : canCurate
                      ? "자료를 올리거나 채택에서 빼면, 그 자료를 인용한 절이 개요에서 미반영으로 표시됩니다. 다시 쓸지는 거기서 고르시면 됩니다."
                      : "실행 중에는 자료를 바꿀 수 없습니다 - 작성이 쓰고 있는 자료 집합이 도중에 바뀌면 절마다 근거가 달라집니다."}
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
              <UploadDropzone onFiles={handleFiles} uploading={transferring} />
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

        {/* 검색 리허설 경고 - 색인 후 절별 실검색에서 근거 공백이 확인돼 게이트가
            다시 열린 경우. 수집 매칭(coverage)보다 강한 실측 신호라 따로 보여준다. */}
        {rehearsal && rehearsal.empty_sections.length > 0 ? (
          <div className="flex flex-col gap-1.5 rounded-md border border-fg-danger/40 bg-bg-danger px-4 py-3 text-sm">
            <p className="flex items-center gap-2 text-fg">
              <AlertTriangle className="h-4 w-4 shrink-0 text-fg-danger" aria-hidden />
              검색 리허설에서 근거가 부족한 절이 확인됐습니다 - 자료를 보강하거나 그대로 진행하세요.
              (재확인 {rehearsal.reopens_used + 1}/2회차)
            </p>
            <ul className="flex flex-col gap-0.5 text-xs text-fg-secondary">
              {rehearsal.empty_sections.map((s) => (
                <li key={s.label}>
                  <span className="font-medium text-fg">{s.label}</span> - 근거 {s.floor_passed}/
                  {s.needed}건
                  {s.constructive
                    ? " (구성형 절: 앞 절 산출로 쓰는 절이라 자료 보강으로는 안 채워집니다)"
                    : s.raptor_gap
                      ? " (수집 자료 전체에도 유사 내용 없음 - 해당 주제 자료 업로드 권장)"
                      : " (자료는 있으나 검색에 안 걸림 - 절 제목·방향 표현을 바꾸면 나아질 수 있음)"}
                </li>
              ))}
            </ul>
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
                    canCurate ? (
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
                          onClick={() => void requestExclude(s)}
                        >
                          {s.is_included === false
                            ? "제외됨"
                            : checkingImpact === s.id
                              ? "확인 중…"
                              : "제외"}
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

      {/* 추가 검색은 게이트와 무관하게 늘 눌린다 - 자료를 더 모으는 일에 '검토 대기'가
          전제일 이유가 없다. '검토 완료'만 게이트가 열렸을 때 뜬다: 멈춰 선 파이프라인이
          없으면 확정할 대상 자체가 없다(2026-08-26 행동·게이트 분리). */}
      {canCurate || reviewOpen ? (
        <FinalizeBar
          canFinalize={canFinalize}
          isPending={handleFinalize.isPending}
          onUploadFocus={() => window.scrollTo({ top: 0, behavior: "smooth" })}
          onFinalize={reviewOpen ? () => handleFinalize.mutate() : undefined}
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
        readOnly={!canCurate}
        onInclude={(sid) => setIncluded(sid, true)}
        onExclude={(sid) => {
          // 상세창의 제외도 같은 문을 지난다 - 한쪽만 확인을 받으면 사람은 확인이
          // 없는 쪽으로 몰린다.
          const target = items.find((x) => x.id === sid);
          if (target) void requestExclude(target);
        }}
      />
      <ExcludeImpactDialog
        projectId={projectId}
        source={excludeTarget}
        pending={patchSource.isPending}
        onCancel={() => setExcludeTarget(null)}
        onConfirm={() => {
          if (excludeTarget) setIncluded(excludeTarget.id, false);
          setExcludeTarget(null);
        }}
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
  /** 게이트가 열렸을 때만 준다 - 없으면 확정 버튼을 안 그린다. */
  onFinalize?: () => void;
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
          {onFinalize ? (
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
          ) : (
            // 게이트가 없으면 확정할 대상이 없다 - 고친 내용은 즉시 반영되고, 영향받은
            // 절은 개요의 미반영 카드가 알린다.
            <span className="text-xs text-fg-tertiary">
              변경은 바로 반영됩니다 · 영향받은 절은 개요에서 확인
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

/** 파일 자료 한 줄 요약 - "용량 · 쪽수 · 조각 수". 조각 수가 "본문이 실제로
 * 들어갔나"를 가르는 신호다(파일명만으로는 알 수 없다). */
function fileFacts(s: Source): string {
  const parts: string[] = [];
  if (s.size_bytes) {
    parts.push(formatSize(s.size_bytes));
  }
  if (s.page_count) parts.push(`${s.page_count}쪽`);
  if (s.indexing) parts.push("색인 중…");
  else if (s.n_chunks !== undefined) {
    parts.push(
      s.n_chunks > 0 ? `조각 ${s.n_chunks.toLocaleString()}개` : "조각 0 - 다시 올려주세요",
    );
  }
  if (s.index_error) parts.push(`색인 실패: ${s.index_error}`);
  return parts.join(" · ");
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
          <div className="flex min-w-0 flex-1 flex-col">
            <span className="truncate text-sm text-fg">{s.title}</span>
            <span className="truncate text-[11px] text-fg-tertiary">{fileFacts(s)}</span>
          </div>
          {s.indexing ? (
            <Badge variant="secondary" className="shrink-0 text-xs font-normal">
              색인 중
            </Badge>
          ) : s.index_deferred && !s.n_chunks ? (
            // 실행 전 업로드 - 색인은 보고서 실행의 색인 단계에서 GPU로 한꺼번에 돈다.
            <Badge variant="secondary" className="shrink-0 text-xs font-normal">
              실행 시 색인
            </Badge>
          ) : s.n_chunks === 0 ? (
            // 파일은 올라갔는데 본문을 못 뽑은 상태 - 파일명만 보면 정상과 구분이 안 된다
            // (2026-08-20: 동시 색인 메모리 부족으로 8건이 조용히 이 상태가 됐다).
            <Badge variant="destructive" className="shrink-0 text-xs font-normal">
              본문 없음
            </Badge>
          ) : null}
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
