import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  BarChart3,
  CheckCircle2,
  ChevronLeft,
  ExternalLink,
  Eye,
  FileSearch,
  History,
  Image as ImageIcon,
  Lock,
  LockOpen,
  Pencil,
  Save,
  Sparkles,
  Table2,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import {
  parseQaSelectPayload,
  type QaSelectPayload,
  type QaSelectWarning,
  useDecideQaSelect,
} from "@/api/checkpoints";
import { ApiError } from "@/api/client";
import { estimateLabel, formatUsd, useCostBasis } from "@/api/cost";
import { progressKeys, useConfirmedStalled, useProgressSnapshot } from "@/api/progress";
import { useProject } from "@/api/projects";
import {
  useProjectSections,
  useRewriteBlock,
  useRewriteSection,
  useSaveSection,
  useSectionContent,
  useSectionEvidence,
  useSetSectionLock,
} from "@/api/sections";
import type {
  ChapterNode,
  FigurePlaceholder,
  SectionCitation,
  SectionNode,
  SectionStatus,
} from "@/api/types";
import { useVerifyReport, verifyKeys } from "@/api/verify";
import type { SectionHistoryEntry } from "@/api/versions";
import { StatusDot, type StatusKind } from "@/components/data-display/StatusDot";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { HelpTip } from "@/components/ui/help-tip";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { VerifyReportCard } from "@/features/export/VerifyReportCard";
import { ChartConvertDialog } from "@/features/preview/ChartConvertDialog";
import { claimTone } from "@/features/preview/ClaimHoverCard";
import { chartFallbackTable } from "@/features/preview/chartSpec";
import { BlockEvidence, partitionBlockEvidence } from "@/features/preview/EvidencePanel";
import { MarkdownContent } from "@/features/preview/MarkdownContent";
import { appendSourceNumber, removeSourceNumber } from "@/features/preview/markers";
import { SectionVariantsPanel, SectionVariantsTrigger } from "@/features/preview/SectionVariants";
import { type SourceLocation, SourceViewer } from "@/features/preview/SourceViewer";
import {
  defaultChoice,
  findTable,
  isTableCaption,
  type MarkdownTable,
} from "@/features/preview/tableToChart";
import { SectionHistoryPanel } from "@/features/versions/SectionHistoryPanel";
import { VersionDiffView } from "@/features/versions/VersionDiffView";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";

const EVIDENCE_UI_ENABLED = true;

const STATUS_KIND: Record<SectionStatus, StatusKind> = {
  pending: "tertiary",
  writing: "info",
  completed: "success",
  failed: "danger",
};

/** 본문이 존재해 열람 가능한 상태 - writing은 작성 중 증분 초안. */
const VIEWABLE: SectionStatus[] = ["completed", "writing"];

// 정적검사 체크 이름 → 사람용 라벨 (백엔드 services/qa/gate.py 어휘)
const WARNING_LABEL: Record<string, string> = {
  bounds: "분량·금칙어",
  numeric_grounded: "수치 근거",
  citation_resolves: "인용 해석",
  citation_markers: "인용 표기",
  uncited_claims: "무근거 주장",
  renderable: "렌더 가능",
};

/** 보고서 통합 작업공간 - 미리보기·본문 검토(QA)·편집을 한 화면으로(2026-08-07).
 *
 * 작성이 진행되는 대로 완성된 절부터 표시되고, 그 자리에서 블록 단위 직접
 * 편집·AI 재작성이 가능하다. QA 게이트가 열려 있으면 상단 승인 바에서 바로
 * 조립을 시작한다 - 검토 중 편집은 resume 시 overlay_working_copy가 반영한다.
 */
/** 보고서 작업공간 - 개요 페이지에 인라인으로 박히고, /preview 단독 페이지도 이걸 쓴다.
 *  화면을 나누지 않기 위해 셸(AppShell·헤더)과 분리했다(2026-08-09 사용자 결정). */
export function ReportWorkspace({ projectId }: { projectId: string }) {
  const [params, setParams] = useSearchParams();

  const projectQuery = useProject(projectId, 7000);
  const projectStatus = projectQuery.data?.status;
  // 생성 진행 중이면 트리를 폴링 - 절이 완성되는 대로 순차 표시(증분 미리보기)
  // 폴링이 필요한 단계인가 - **표시 판단이 아니라 갱신 주기**에만 쓴다.
  // 단계 이름으로 "도는 중"을 판정하면 안 된다: status는 서 있는 자리지 실행 신호가
  // 아니다(2026-08-26 파생화). 실제 실행 여부는 아래 runner_alive가 답한다.
  const maybeActive =
    projectStatus === "researching" ||
    projectStatus === "indexing" ||
    projectStatus === "writing" ||
    projectStatus === "reviewing";
  const sectionsQuery = useProjectSections(projectId, maybeActive ? 5000 : undefined);

  // QA 게이트 대기 감지 - 열려 있으면 상단 승인 바 + 절별 정적검사 경고 표시
  const snapshotQuery = useProgressSnapshot(projectId, true, {
    refetchInterval: maybeActive ? 7000 : false,
  });
  // **실제로 도는가** - 편집 잠금·표시의 유일한 근거. 단계 이름이 아니다.
  // 다 쓴 보고서가 'reviewing'이라는 이유로 읽기 전용이 되던 회귀가 여기서 났다.
  const [verifyOpen, setVerifyOpen] = useState(params.get("verify") === "1");
  // 상태 패널의 '경고 보기'가 ?verify=1을 붙인다 - 다른 열에 있는 컴포넌트라 프롭을
  // 깊게 넘기는 대신 URL로 잇는다. 열고 나서 지운다: 안 지우면 닫았다가 다시 눌러도
  // URL이 그대로라 아무 일도 안 일어난다(버전 시트에서 겪은 것과 같은 함정).
  useEffect(() => {
    if (params.get("verify") !== "1") return;
    setVerifyOpen(true);
    const next = new URLSearchParams(params);
    next.delete("verify");
    setParams(next, { replace: true });
  }, [params, setParams]);
  const running = snapshotQuery.data?.runner_alive ?? false;
  // 죽은 런(백엔드 태스크 소실) - '작성 중' 스피너 배너를 중단 안내로 바꾼다.
  const stalled = useConfirmedStalled(snapshotQuery.data);
  const qaPayload = useMemo(
    () =>
      snapshotQuery.data?.pending_gate?.gate === "qa_select"
        ? parseQaSelectPayload(snapshotQuery.data.pending_gate.payload)
        : null,
    [snapshotQuery.data],
  );

  // 근거 드로어가 열리면 절 트리를 접는다 - 1440px 화면에서 좌측 내비(180) + 트리(240)
  // + 드로어(460)가 본문을 560px까지 밀어낸다(실측). 근거를 대조하는 동안 트리는 쓰지
  // 않으므로 그 자리를 내주면 본문이 온전히 남는다.
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  const handleEvidenceOpenChange = useCallback((open: boolean) => setEvidenceOpen(open), []);
  const tree = sectionsQuery.data?.tree ?? [];
  const selectedId = params.get("section") ?? findFirstViewable(tree);
  // 버전 비교 모드 - URL 파라미터(?compare=1)라 새로고침·링크 공유에도 유지된다.
  const compareBase = useMemo(() => {
    const raw = params.get("compare");
    const n = raw ? Number.parseInt(raw, 10) : Number.NaN;
    return Number.isFinite(n) && n > 0 ? n : null;
  }, [params]);
  // 견줄 상대 - 없으면 현재 본문과 비교(가장 흔한 용례).
  const compareTarget = useMemo(() => {
    const raw = params.get("compareTo");
    const n = raw ? Number.parseInt(raw, 10) : Number.NaN;
    return Number.isFinite(n) && n > 0 ? n : null;
  }, [params]);
  // 선택된 id가 '장'이면 장 통합 뷰(하위 절 이어 보기), 아니면 절 본문.
  const selectedChapter = useMemo(
    () => tree.find((c) => c.id === selectedId) ?? null,
    [tree, selectedId],
  );
  // 장 선택 시엔 절 본문 쿼리를 끈다(장 id로는 본문이 없어 헛요청이 됨).
  const contentQuery = useSectionContent(projectId, selectedChapter ? null : selectedId);

  // 선택된 절의 QA 정적검사 경고(게이트 payload) - 검토 중에만 존재한다.
  const qaWarnings = useMemo<QaSelectWarning[]>(() => {
    if (!qaPayload || !selectedId) return [];
    const sec = qaPayload.sections.find((s) => s.section_id === selectedId);
    return sec?.candidates[0]?.warnings ?? [];
  }, [qaPayload, selectedId]);

  // 선택된 절의 PM 경고만 본문 위에 인라인 표시 - 고칠 대상 옆에 고칠 이유를 둔다.
  const verifyQuery = useVerifyReport(projectId, true, running);
  // 완료 전이 순간에 한 번 더 - 생성 중 폴링(4초)의 마지막 회차와 pm_verify 저장
  // 사이의 레이스를 닫는다. 전이 후엔 폴링이 꺼지므로 이게 없으면 그 창에 쓰인
  // 경고는 새로고침 전까지 안 보인다.
  const verifyQc = useQueryClient();
  useEffect(() => {
    if (projectStatus === "completed" || projectStatus === "archived") {
      void verifyQc.invalidateQueries({ queryKey: verifyKeys.report(projectId) });
    }
  }, [projectStatus, projectId, verifyQc]);
  const selectedRef = useMemo(() => {
    if (!selectedId) return null;
    for (const [ci, ch] of tree.entries()) {
      for (const [si, s] of ch.children.entries()) {
        if (s.id === selectedId) return `${ci + 1}.${si + 1}`;
      }
    }
    return null;
  }, [tree, selectedId]);
  const sectionFindings = useMemo(() => {
    if (!selectedId || !verifyQuery.data) return [];
    return verifyQuery.data.filter((f) => {
      // 절 안정 id가 정본 - 목차가 바뀌어도 경고가 그 절을 따라간다.
      if (f.section_id) return f.section_id === selectedId;
      // id 없는 옛 경고는 번호 표기로 폴백
      if (!selectedRef) return false;
      const ref = f.section_ref ?? "";
      if (!ref.startsWith(selectedRef)) return false;
      const next = ref[selectedRef.length];
      return next === undefined || !/[0-9]/.test(next); // '1.1'이 '1.12'에 오매칭되지 않게
    });
  }, [verifyQuery.data, selectedId, selectedRef]);
  const navigateTo = (id: string) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("section", id);
        return next;
      },
      { replace: true },
    );

  // PM 경고 목록에서 §1.1을 누르면 그 절로 이동 - 트리에서 손으로 찾지 않게.
  // 절 안정 id가 정본이고, id 없는 옛 경고만 번호를 위치로 해석한다(장 번호가
  // 비연속이면 엉뚱한 절로 가던 경로 - id가 있으면 그 문제가 없다).
  const jumpToRef = (target: { section_ref?: string | null; section_id?: string | null }) => {
    if (target.section_id) {
      const hit = tree.some((ch) => ch.children.some((s) => s.id === target.section_id));
      if (hit) {
        navigateTo(target.section_id);
        return;
      }
    }
    const ref = target.section_ref ?? "";
    const head = ref.split(" ")[0]; // "1.1 표 1-2-1" 같은 표기에서 앞부분만
    const [ch, sec] = head.split(".").map((n) => Number.parseInt(n, 10));
    const node = tree[ch - 1]?.children[sec - 1];
    if (!node) {
      toast("해당 절을 찾지 못했습니다", { description: ref || "위치 정보 없음" });
      return;
    }
    navigateTo(node.id);
  };

  const selectNode = (id: string, status: SectionStatus, isChapter: boolean) => {
    // 장은 항상 이동(하위 절 통독 뷰가 완성분만 골라 보여줌). 절은 작성 중에는 본문
    // 있는 것만 - 생성이 끝난 뒤에는 빈 절도 열린다. 막아 두면 작성 실패한 절의
    // 재작성 진입점이 없다(2026-08-14 지적: 빈 절에 재작성 버튼이 없음).
    if (!isChapter && !VIEWABLE.includes(status) && running) {
      toast("아직 작성되지 않은 절입니다", {
        description: "작성이 완료되는 대로 순서대로 표시됩니다.",
      });
      return;
    }
    navigateTo(id);
  };

  return (
    <div id="report-body" className="flex flex-col gap-4">
      {qaPayload ? (
        <QaApproveBar projectId={projectId} payload={qaPayload} />
      ) : stalled ? (
        // 실행이 끊겼는데 '진행 중' 스피너를 계속 돌리면 사용자는 하염없이 기다린다
        // (2026-08-12 실사용: 3시간 넘게 진행 중으로 보임). 재개 경로를 바로 안내한다.
        <div className="flex items-center gap-2 rounded border border-fg-danger/30 bg-bg-danger px-3 py-2 text-xs text-fg">
          <AlertTriangle className="h-4 w-4 shrink-0 text-fg-danger" aria-hidden />
          실행이 중단되었습니다 - 서버 재시작이나 오류로 실행이 끊겼습니다. 개요 화면의 '이어서
          재개'를 누르면 멈춘 단계부터 다시 진행합니다.
        </div>
      ) : null}
      {/* '작성 진행 중' 배너는 걷어냈다(2026-08-26 사용자 결정) - 개요 상단 상태
          표시가 같은 말을 이미 하고, 두 군데서 하면 어긋날 때 어느 쪽이 맞는지 모른다. */}

      {/* PM 검증 경고 - 고칠 수 있는 화면에 두되 접힌 한 줄로 시작(편집을 가리지 않게).
            절을 선택하면 그 절의 경고만 본문 위에 인라인 표시된다. */}
      {/* 앵커 - 상태 패널의 '경고 보기'가 이 자리로 데려온다. 재검증 버튼은 경고를
          보고 있는 이 배너에만 둔다(패널에도 두면 같은 일을 하는 버튼이 둘이다). */}
      <div id="pm-verify">
        <VerifyReportCard
          projectId={projectId}
          collapsible
          onJump={jumpToRef}
          onExpand={() => setVerifyOpen(true)}
        />
      </div>

      {/* 경고 목록은 **오른쪽에 펼친다**. 본문 위에 펴면 글이 아래로 밀려, 경고를 읽는
          동안 정작 고칠 문장이 안 보인다(2026-08-27 지적). modal=false라 열어 둔 채로
          본문을 만질 수 있고, 항목을 눌러 절을 옮겨 다니는 동안 목록이 그대로 있다. */}
      <Sheet open={verifyOpen} onOpenChange={setVerifyOpen} modal={false}>
        <SheetContent
          side="right"
          overlay={false}
          className="w-full overflow-y-auto shadow-2xl sm:max-w-lg"
          onInteractOutside={(e) => e.preventDefault()}
        >
          <SheetHeader>
            <SheetTitle>PM 검증 경고</SheetTitle>
          </SheetHeader>
          <div className="mt-4">
            <VerifyReportCard projectId={projectId} onJump={jumpToRef} />
          </div>
        </SheetContent>
      </Sheet>

      {/* 버전 기록 목록은 개요 상태 패널의 '기록 열기'(시트)로 옮겼다 - 여기 끼워
            두면 PM 경고와 본문 사이를 가로막아 읽는 흐름이 끊긴다(2026-08-27 지적).
            비교(diff)는 넓은 자리가 필요해 아래 본문 영역을 그대로 쓴다. */}

      {compareBase !== null ? (
        <VersionDiffView
          projectId={projectId}
          base={compareBase}
          target={compareTarget}
          onChange={(b, t) =>
            setParams(
              (prev) => {
                const next = new URLSearchParams(prev);
                next.set("compare", String(b));
                if (t === null) next.delete("compareTo");
                else next.set("compareTo", String(t));
                return next;
              },
              { replace: true },
            )
          }
          onClose={() =>
            setParams(
              (prev) => {
                const next = new URLSearchParams(prev);
                next.delete("compare");
                next.delete("compareTo");
                return next;
              },
              { replace: true },
            )
          }
        />
      ) : sectionsQuery.isLoading ? (
        <LoadingSkeleton variant="block" />
      ) : tree.length === 0 && qaPayload ? (
        <PayloadDraftList payload={qaPayload} />
      ) : tree.length === 0 && running ? (
        <EmptyState
          icon={Eye}
          title="아직 완성된 절이 없습니다"
          description="본문 작성이 시작되면 완성된 절부터 여기에 순서대로 표시됩니다."
        />
      ) : sectionsQuery.isError || tree.length === 0 ? (
        <EmptyState
          title="섹션 트리를 불러올 수 없습니다"
          description="잠시 후 다시 시도해 주세요."
          action={
            <Button variant="outline" onClick={() => void sectionsQuery.refetch()}>
              다시 시도
            </Button>
          }
        />
      ) : (
        <div
          className={cn(
            "grid grid-cols-1 gap-4",
            // 트리를 접는 건 자리가 없을 때뿐이다. 좌측 내비(180) + 트리(240) + 본문(~900)
            // + 드로어(560)가 1,880px쯤 필요하니, 그보다 넓으면 셋 다 그대로 둔다.
            evidenceOpen
              ? "min-[1900px]:grid-cols-[240px_minmax(0,1fr)]"
              : "min-[1200px]:grid-cols-[240px_minmax(0,1fr)]",
          )}
        >
          {/* 트리는 내부 스크롤 없이 아래로 쭉 펼친다 - 목차 전체가 한눈에 보이고
                페이지 스크롤 하나로 탐색한다(사용자 요청, 2026-08-04).
                근거 드로어가 열려 있는 동안에는 접어 본문에 자리를 내준다. */}
          <aside
            className={cn(
              "self-start rounded border border-border bg-bg",
              evidenceOpen && "hidden min-[1900px]:block",
            )}
          >
            <SectionTree tree={tree} selectedId={selectedId} onSelect={selectNode} />
          </aside>

          <main className="rounded border border-border bg-bg">
            {selectedId && sectionFindings.length > 0 ? (
              <div className="flex flex-col gap-1.5 border-b border-fg-warning/30 bg-bg-warning px-4 py-3">
                <p className="flex items-center gap-1.5 text-xs font-medium text-fg">
                  <AlertTriangle className="h-3.5 w-3.5 text-fg-warning" aria-hidden />이 절의 PM
                  경고 {sectionFindings.length}건
                </p>
                <ul className="flex flex-col gap-1">
                  {sectionFindings.map((f) => (
                    <li key={f.id} className="text-xs text-fg-secondary">
                      <span
                        className={cn(
                          "mr-1 font-medium",
                          f.severity === "critical" ? "text-fg-danger" : "text-fg-warning",
                        )}
                      >
                        [{f.category}]
                      </span>
                      {f.detail}
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
            {selectedChapter ? (
              <ChapterView
                key={selectedChapter.id}
                projectId={projectId}
                chapter={selectedChapter}
                chapterIndex={tree.indexOf(selectedChapter)}
                onOpenSection={navigateTo}
                editable={!running}
              />
            ) : selectedId ? (
              <SectionView
                key={selectedId}
                projectId={projectId}
                sectionId={selectedId}
                contentQuery={contentQuery}
                qaWarnings={qaWarnings}
                editable={!running}
                onEvidenceOpenChange={handleEvidenceOpenChange}
              />
            ) : (
              <EmptyState
                title="섹션을 선택하세요"
                description="좌측 트리에서 완성된 섹션을 클릭하면 본문이 표시됩니다."
              />
            )}
          </main>
        </div>
      )}
    </div>
  );
}

/** /preview 단독 진입 - 개요에 인라인된 것과 같은 작업공간을 셸에 감싸 보여준다. */
export default function PreviewPage() {
  const { id: projectId = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { user, logout } = useAuth();
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
      <ReportWorkspace projectId={projectId} />
    </AppShell>
  );
}

/** QA 승인 바 - 게이트가 열려 있는 동안만 표시. 별도 검토 페이지를 대체한다.
 *
 * n=1 전환으로 '후보 고르기'는 없다 - 절당 유일 초안을 자동 채택해 제출한다.
 * 검토 중 화면에서 고친 내용은 백엔드가 조립 시 payload보다 우선 반영한다. */
function QaApproveBar({ projectId, payload }: { projectId: string; payload: QaSelectPayload }) {
  const qc = useQueryClient();
  const decide = useDecideQaSelect();

  const reviewable = useMemo(
    () => payload.sections.filter((s) => !s.all_excluded && s.candidates.length > 0),
    [payload],
  );
  const excludedCount = payload.sections.length - reviewable.length;

  const onSubmit = () => {
    if (decide.isPending) return;
    const picked = Object.fromEntries(
      reviewable.map((s) => [s.section_id, s.candidates[0].candidate_id]),
    );
    decide.mutate(
      { projectId, selections: picked },
      {
        onSuccess: () => {
          toast.success("검토가 완료됐습니다.", {
            description: "검토한 본문으로 보고서 조립을 시작합니다.",
          });
          void qc.invalidateQueries({ queryKey: progressKeys.snapshot(projectId) });
        },
        onError: (err) => {
          const msg = err instanceof ApiError ? err.message : "제출에 실패했습니다.";
          toast.error("검토 제출 실패", { description: msg });
        },
      },
    );
  };

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded border border-accent bg-bg-info px-4 py-3">
      <div className="flex flex-col gap-0.5">
        <p className="flex items-center gap-1.5 text-sm font-medium text-fg">
          <CheckCircle2 className="h-4 w-4 text-fg-info" aria-hidden />
          본문 검토 대기 - 절을 열어 확인·수정한 뒤 승인하면 조립이 시작됩니다.
        </p>
        <p className="text-xs text-fg-secondary">
          완성 {reviewable.length}절
          {excludedCount > 0
            ? ` · 빈 절 ${excludedCount}개 (여기서 직접 작성해 채우거나, 비운 채 조립할 수 있습니다)`
            : ""}
        </p>
      </div>
      <Button onClick={onSubmit} disabled={decide.isPending}>
        {decide.isPending ? "제출 중…" : `검토 완료 · 조립 시작 (${reviewable.length}절)`}
      </Button>
    </div>
  );
}

/** 게이트 payload 폴백 - sections 행이 아직 없는 검토(증분 저장 배선 전 백엔드로
 * 작성된 런)에서 초안을 읽기 전용으로 나열한다. 블록 편집은 행이 생긴 뒤부터. */
function PayloadDraftList({ payload }: { payload: QaSelectPayload }) {
  const planById = new Map(payload.section_plan.map((p) => [p.section_id, p]));
  return (
    <div className="flex flex-col gap-4">
      {payload.sections.map((sec) => {
        const plan = planById.get(sec.section_id);
        const cand = sec.candidates[0];
        return (
          <section key={sec.section_id} className="rounded border border-border bg-bg p-4">
            <h3 className="mb-2 text-base font-semibold text-fg">
              {plan
                ? `${plan.chapter_number}.${plan.section_number} ${plan.title}`
                : sec.section_id}
            </h3>
            {cand ? (
              <div className="max-h-80 overflow-y-auto rounded border border-border bg-bg-secondary p-3 text-sm leading-relaxed">
                <MarkdownContent content={cand.content} />
              </div>
            ) : (
              <p className="text-sm text-fg-tertiary">
                정적검사를 통과한 초안이 없습니다 - 비운 채 조립됩니다.
              </p>
            )}
          </section>
        );
      })}
    </div>
  );
}

/** 절 하단 출처 목록 - 본문 [N]이 가리키는 자료를 번호순으로 나열한다. */
/** 이 절에 들어갈 그림 자리 - 한글 산출물에만 있어 화면에서는 안 보이던 것.
 *
 * 자리표시자는 조립 단계에서 만들어져 본문(content)에 없다. 그래서 검토자는 한글
 * 파일을 열기 전까지 어디에 그림이 들어갈지 몰랐다(2026-08-24 지적). 위치까지
 * 재현하지는 않는다 - "몇 개, 무슨 소재로, 원본은 어디"까지가 화면의 몫이다. */
function FigurePlaceholderList({ figures }: { figures: FigurePlaceholder[] }) {
  if (figures.length === 0) return null;
  return (
    <section className="mt-6 rounded-md border border-dashed border-border bg-bg-secondary p-3">
      <h3 className="mb-2 flex items-center gap-1.5 text-xs font-medium text-fg-secondary">
        <ImageIcon className="h-3.5 w-3.5 shrink-0" />
        한글 출력에 들어갈 그림 자리 {figures.length}개
      </h3>
      <ol className="flex flex-col gap-2">
        {figures.map((f) => (
          <li key={f.caption} className="text-xs">
            <p className="text-fg">{f.caption}</p>
            <p className="mt-0.5 text-fg-tertiary">{f.description}</p>
            {f.source_hints.length > 0 ? (
              <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-fg-tertiary">
                <span className="shrink-0">원본 참고:</span>
                {f.source_hints.map((hint) => {
                  const url = hint.match(/\((https?:\/\/[^)]+)\)$/)?.[1];
                  const label = url ? hint.slice(0, hint.lastIndexOf("(")).trim() : hint;
                  return url ? (
                    <a
                      key={hint}
                      href={url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 text-fg-info hover:underline"
                    >
                      {label}
                      <ExternalLink className="h-3 w-3 shrink-0" />
                    </a>
                  ) : (
                    <span key={hint}>{label}</span>
                  );
                })}
              </p>
            ) : null}
          </li>
        ))}
      </ol>
    </section>
  );
}

function CitationList({ citations }: { citations: SectionCitation[] }) {
  if (citations.length === 0) return null;
  // 번호는 대목(청크) 단위라 같은 자료가 여러 번호를 받는다 - 목록은 자료 단위로
  // 묶어 번호를 나란히 보여준다(같은 제목이 줄줄이 반복되던 문제, 2026-08-27).
  const grouped: { key: string; numbers: (number | null)[]; first: SectionCitation }[] = [];
  const byKey = new Map<string, number>();
  for (const c of citations) {
    const key = c.url || c.title || "";
    const at = byKey.get(key);
    if (at === undefined) {
      byKey.set(key, grouped.length);
      grouped.push({ key, numbers: [c.number ?? null], first: c });
    } else {
      grouped[at].numbers.push(c.number ?? null);
    }
  }
  return (
    <footer className="mt-6 border-t border-border pt-3">
      <h3 className="mb-2 text-xs font-medium text-fg-secondary">
        이 절의 출처 {grouped.length}건
      </h3>
      <ol className="flex flex-col gap-1">
        {grouped.map(({ key, numbers, first: c }) => (
          <li key={`cite-${key}`} className="flex items-start gap-2 text-xs">
            <span className="shrink-0 font-mono text-fg-tertiary">
              {numbers.map((n) => `[${n ?? "–"}]`).join(" ")}
            </span>
            {c.url ? (
              <a
                href={c.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-fg-info hover:underline"
              >
                {c.title}
                <ExternalLink className="h-3 w-3 shrink-0" />
              </a>
            ) : (
              <span className="text-fg-secondary">{c.title}</span>
            )}
          </li>
        ))}
      </ol>
    </footer>
  );
}

function findFirstViewable(tree: ChapterNode[]): string | null {
  for (const ch of tree) {
    for (const sec of ch.children) {
      if (VIEWABLE.includes(sec.status)) return sec.id;
    }
    if (VIEWABLE.includes(ch.status)) return ch.id;
  }
  return null;
}

interface SectionTreeProps {
  tree: ChapterNode[];
  selectedId: string | null;
  onSelect: (id: string, status: SectionStatus, isChapter: boolean) => void;
}

function SectionTree({ tree, selectedId, onSelect }: SectionTreeProps) {
  return (
    <nav aria-label="섹션 트리" className="flex flex-col p-2">
      {tree.map((chapter, chIdx) => (
        <div key={chapter.id} className="mb-2">
          <TreeItem
            id={chapter.id}
            label={`${chIdx + 1}장`}
            title={chapter.title}
            level={1}
            status={chapter.status}
            selected={selectedId === chapter.id}
            onSelect={onSelect}
          />
          <ul className="ml-2 flex flex-col">
            {chapter.children.map((section, secIdx) => (
              <li key={section.id}>
                <TreeItem
                  id={section.id}
                  label={`${chIdx + 1}.${secIdx + 1}`}
                  title={section.title}
                  level={2}
                  status={section.status}
                  selected={selectedId === section.id}
                  onSelect={onSelect}
                />
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}

function TreeItem({
  id,
  label,
  title,
  level,
  status,
  selected,
  onSelect,
}: {
  id: string;
  /** 사람이 읽는 순번(1장, 1.1) - 실데이터 id는 UUID라 라벨로 못 쓴다 */
  label: string;
  title: SectionNode["title"];
  level: 1 | 2;
  status: SectionStatus;
  selected: boolean;
  onSelect: (id: string, status: SectionStatus, isChapter: boolean) => void;
}) {
  const dim = !VIEWABLE.includes(status);
  return (
    <button
      type="button"
      onClick={() => onSelect(id, status, level === 1)}
      className={cn(
        "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm transition-colors",
        level === 1 ? "font-medium" : "pl-6 text-xs",
        selected ? "border border-accent bg-bg-info text-fg" : "border border-transparent",
        dim ? "text-fg-tertiary" : "text-fg",
        !selected && "hover:bg-bg-secondary",
      )}
    >
      <span className="shrink-0 font-mono text-[11px] text-fg-tertiary">{label}</span>
      <span className="line-clamp-1 flex-1">{title}</span>
      <StatusDot kind={STATUS_KIND[status]} />
    </button>
  );
}

/** 장 통합 뷰 - 그 장의 완성된 절들을 이어서 표시(읽기 전용). 편집은 개별 절을 연다. */
function ChapterView({
  projectId,
  chapter,
  chapterIndex,
  onOpenSection,
  editable,
}: {
  projectId: string;
  chapter: ChapterNode;
  chapterIndex: number;
  onOpenSection: (sectionId: string) => void;
  /** 생성이 끝났는가 - 끝났으면 미작성 절의 재작성 진입점(열기 버튼)을 노출한다 */
  editable: boolean;
}) {
  const completed = chapter.children.filter((s) => VIEWABLE.includes(s.status));
  // 작성 실패·미작성 절 - 생성이 끝난 뒤에도 남아 있으면 복구 대상이다.
  const unwritten = chapter.children.filter((s) => !VIEWABLE.includes(s.status));
  return (
    <article className="flex flex-col">
      <header className="border-b border-border px-6 py-4">
        <p className="font-mono text-xs text-fg-tertiary">{chapterIndex + 1}장</p>
        {/* 본문 마크다운 h1(text-2xl)보다 작으면 위계가 뒤집혀 보인다(2026-08-14 지적)
            - 장 제목은 이 화면의 최상위라 한 단계 위(3xl)로 둔다. */}
        <h2 className="text-3xl font-semibold text-fg">{chapter.title}</h2>
        <p className="mt-1 text-xs text-fg-secondary">
          완성된 절 {completed.length}/{chapter.children.length}개 - 이어서 표시(읽기 전용). 편집은
          각 절을 여세요.
        </p>
      </header>
      {editable && unwritten.length > 0 ? (
        // 빈 절을 "없음"으로만 표시하면 복구 경로가 없다(2026-08-14 지적) - 절을 열면
        // 그 화면에서 AI 재작성으로 새로 쓸 수 있다.
        <div className="flex flex-col gap-1.5 border-b border-fg-danger/30 bg-bg-danger px-6 py-3">
          <p className="text-xs font-medium text-fg">작성되지 않은 절 {unwritten.length}개</p>
          <ul className="flex flex-col gap-1">
            {unwritten.map((section) => (
              <li key={section.id} className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-2 text-xs text-fg-secondary">
                  <span className="font-mono text-fg-tertiary">
                    {chapterIndex + 1}.{chapter.children.indexOf(section) + 1}
                  </span>
                  {section.title}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={() => onOpenSection(section.id)}
                >
                  <Sparkles className="mr-1 h-3 w-3" />
                  열어서 다시 쓰기
                </Button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {completed.length === 0 ? (
        <div className="p-6">
          <EmptyState
            icon={Eye}
            title="아직 완성된 절이 없습니다"
            description={
              editable && unwritten.length > 0
                ? "위 목록에서 절을 열면 AI 재작성으로 새로 쓸 수 있습니다."
                : "이 장의 절이 작성 완료되면 여기 이어서 표시됩니다."
            }
          />
        </div>
      ) : (
        <div className="flex flex-col divide-y divide-border">
          {completed.map((section) => (
            <ChapterSectionBlock
              key={section.id}
              projectId={projectId}
              section={section}
              refLabel={`${chapterIndex + 1}.${chapter.children.indexOf(section) + 1}`}
              onOpen={() => onOpenSection(section.id)}
            />
          ))}
        </div>
      )}
    </article>
  );
}

/** 장 통합 뷰의 절 1개 - 본문을 읽기 전용으로 렌더하고 '편집'으로 그 절 편집 화면을 연다. */
function ChapterSectionBlock({
  projectId,
  section,
  refLabel,
  onOpen,
}: {
  projectId: string;
  section: SectionNode;
  refLabel: string;
  onOpen: () => void;
}) {
  const query = useSectionContent(projectId, section.id);
  return (
    <section className="px-6 py-5">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 text-base font-semibold text-fg">
          <span className="font-mono text-xs text-fg-tertiary">{refLabel}</span>
          {section.title}
          {section.evidence_scarce ? (
            <Badge
              variant="secondary"
              className="bg-bg-warning text-fg-warning"
              title="확보된 근거가 적어 분량을 줄여 작성한 절입니다"
            >
              자료 부족
            </Badge>
          ) : null}
        </h3>
        <Button variant="ghost" size="sm" onClick={onOpen}>
          <Pencil className="mr-1 h-4 w-4" />
          편집
        </Button>
      </div>
      {query.isLoading ? (
        <LoadingSkeleton variant="row" count={4} />
      ) : query.error ? (
        <p className="text-sm text-fg-tertiary">
          {query.error instanceof ApiError ? query.error.message : "본문을 불러오지 못했습니다."}
        </p>
      ) : query.data ? (
        <>
          <MarkdownContent content={query.data.content} citations={query.data.citations} />
          <FigurePlaceholderList figures={query.data.figures} />
          <CitationList citations={query.data.citations} />
        </>
      ) : null}
    </section>
  );
}

/** 마크다운 본문 → 블록 목록. 빈 줄 경계로 나누되 각 블록은 원문의 정확한
 * 부분 문자열로 유지한다 - 블록 치환(직접 편집·AI 재작성)의 전제 조건.
 *
 * 세 가지를 붙여 둔다.
 * - 코드펜스(```chart 등)는 빈 줄이 들어 있어도 통째로 한 블록이다. 반으로 잘린 펜스는
 *   그래프로 그려지지도, 표로 되돌려지지도 않는다.
 * - **표 제목 줄은 표와 한 블록이다.** 제목은 표 위 문단으로 따로 쓰이지만 표의 일부다.
 *   떼어 놓으면 제목만 고르면 그래프 변환이 안 열리고, 표만 바꾸면 그림 위에 "표:"가
 *   남으며, 렌더러는 제목 없는 표로 보고 머리행으로 제목을 하나 더 지어낸다(제목 2개).
 * - **인용 마커만 있는 문단은 앞 블록에 붙인다.** 표 아래 "(출처 n)" 단독 줄이 별도
 *   블록이 되면 근거 배지가 표가 아니라 그 꼬마 블록에 붙고(2026-08-14 사용자 발견),
 *   혼자서는 편집·재작성할 것도 없다. 그래프 변환은 원래 블록의 마커를 따라가게
 *   설계돼 있어(tableToChart.source) 붙여도 무손실이다.
 *
 * 블록은 줄 범위를 잘라 만든다 - 사이 빈 줄까지 그대로 품어야 원문 부분 문자열로 남는다. */
// 불릿·"출처:" 라벨이 앞에 붙은 변형도 받는다 - 작성기가 규약대로 단독 마커만 내지 않고
// "* 출처: (출처 34, 21)"처럼 써 내는 게 실제 출력이라(백엔드 report.py와 같은 관용),
// 안 받으면 표와 빈 줄 하나로 갈라진 출처 줄이 제 블록이 돼 실서지 변환이 통째로 빠지고
// 표 밑이 비면서 인용·근거 배지만 한 벌 더 뜬다(2026-08-15 사용자 지적).
const MARKER_ONLY_BLOCK_RE =
  /^[※*\-–ㅇ○◦\s]*(?:(?:출처|자료|참고)\s*[:：])?(?:\s|\(출처\s*[\d,\s]+\)|\[\d+\])+$/;

// 눌러 담는 재작성 지시. 빈 칸에 대고 무엇을 쓸지 매번 떠올리는 것이 실제 병목이라,
// 자주 쓰는 지시를 버튼으로 둔다(2026-08-26). 고르면 지시 칸에 쌓이고, 손으로 이어
// 붙일 수도 있다 - 대체가 아니라 출발점이다.
// 대상이 절이냐 블록이냐에 따라 쓸모가 갈려 따로 둔다.
// 어느 절에서 눌러도 말이 되는 것만 둔다. '정책 시사점을 앞세워서'는 시장 규모·기술
// 동향 절에서는 뜻이 안 통했다(2026-08-27 지적) - 보고서 유형·절 성격을 가리지 않는
// 지시만 남긴다. 절마다 다른 지시가 필요하면 그건 손으로 적는 자리다.
const SECTION_HINTS = [
  "결론을 앞세워서",
  "수치와 출처를 더 촘촘히",
  "중복 서술을 걷어내고 간결하게",
  "앞 절과 이어지게",
] as const;
const BLOCK_HINTS = [
  "더 간결하게",
  "수치를 앞세워서",
  "근거 표기를 다시 달아서",
  "문장을 개조식으로",
] as const;

function splitBlocks(content: string): string[] {
  const lines = content.split("\n");
  const ranges: Array<[number, number]> = [];
  let start = -1;
  let inFence = false;
  for (let i = 0; i < lines.length; i++) {
    if (/^\s*```/.test(lines[i])) inFence = !inFence;
    if (!inFence && lines[i].trim() === "") {
      if (start >= 0) ranges.push([start, i]);
      start = -1;
      continue;
    }
    if (start < 0) start = i;
  }
  if (start >= 0) ranges.push([start, lines.length]);

  const text = ([a, b]: [number, number]) => lines.slice(a, b).join("\n");
  const merged: Array<[number, number]> = [];
  for (const range of ranges) {
    const prev = merged[merged.length - 1];
    if (prev && isTableCaption(text(prev)) && /^\s*\|/.test(lines[range[0]])) {
      prev[1] = range[1]; // 제목 줄부터 표 끝까지 한 덩어리로
      continue;
    }
    if (prev && MARKER_ONLY_BLOCK_RE.test(text(range))) {
      prev[1] = range[1]; // 출처 표기 단독 문단은 그 표기가 가리키는 앞 블록의 일부다
      continue;
    }
    merged.push(range);
  }
  return merged.map(text);
}

/** 이 블록이 "그래프로 바꿀 만한" 표인가 - 표를 담았고, 값으로 쓸 열이 실제로 있는가.
 *
 * 변환 버튼보다 조건이 좁다. 버튼은 표이기만 하면 열어 주고 왜 안 되는지는 대화상자가
 * 설명하지만, **먼저 말을 거는 쪽**은 되는 것만 권해야 한다. 서술뿐인 표에 "그래프로"를
 * 띄우면 눌러 보고 안 된다는 말만 듣게 된다. */
function chartableTable(block: string): MarkdownTable | null {
  const table = findTable(block);
  if (table === null) return null;
  return defaultChoice(table).seriesCols.length > 0 ? table : null;
}

function SectionView({
  projectId,
  sectionId,
  contentQuery,
  qaWarnings,
  editable,
  onEvidenceOpenChange,
}: {
  projectId: string;
  sectionId: string;
  contentQuery: ReturnType<typeof useSectionContent>;
  qaWarnings: QaSelectWarning[];
  /** 근거 드로어가 열리고 닫힐 때 알린다 - 부모가 절 트리를 접어 자리를 내준다. */
  onEvidenceOpenChange?: (open: boolean) => void;
  /** 생성이 끝나야 편집·재작성을 연다 - 작성 중 초안은 재생성으로 갈아치워질 수 있고,
   *  절 전체 재작성은 검색까지 다시 돌아 작성 루프와 자원을 다툰다(2026-08-09). */
  editable: boolean;
}) {
  const data = contentQuery.data;
  const error = contentQuery.error;

  // 본문 마커에 근거 원문을 붙이려면 절과 함께 받아야 한다(패널과 같은 캐시를 쓴다).
  const evidenceQuery = useSectionEvidence(projectId, sectionId, EVIDENCE_UI_ENABLED);
  const save = useSaveSection(projectId, sectionId);
  const rewrite = useRewriteSection(projectId, sectionId);
  const setLock = useSetSectionLock(projectId, sectionId);
  const costBasis = useCostBasis(projectId);
  const rewriteBlock = useRewriteBlock(projectId, sectionId);

  // 이 절의 이력 - 버전 기록을 절 쪽에서 여는 문(2026-08-27).
  const [historyOpen, setHistoryOpen] = useState(false);
  // 옛 원고를 **본문 자리에서** 읽는 중. 되돌린 게 아니라 잠깐 펴 놓은 것이다 -
  // 이력 칸을 좁게 펴서 읽는 것보다 같은 폭·같은 서식으로 읽는 게 빠르다(2026-08-27).
  const [peek, setPeek] = useState<SectionHistoryEntry | null>(null);
  // 절 전체 직접 편집(기존 동작)
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  // 블록 선택·편집 - 중앙 본문에서 블록을 클릭해 여러 개를 고르고 한 번에 처리한다.
  // 직접 편집은 대상이 하나여야 의미가 있어 단일 선택일 때만 열린다.
  const [selectedIdx, setSelectedIdx] = useState<Set<number>>(new Set());
  const [blockDraft, setBlockDraft] = useState("");
  // 인라인 편집 중인 블록 위치 - 본문 그 자리에서 고친다(절 전체 텍스트박스는 길어서
  // 쓰기 불편하다는 실사용 지적, 2026-08-09).
  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  const [instruction, setInstruction] = useState("");
  // 표→그래프 변환 중인 블록 위치 - 대화상자에서 유형·축을 고르고 저장한다.
  const [convertIdx, setConvertIdx] = useState<number | null>(null);

  // 옛 원고를 보는 중이면 본문 자리에 그 내용을 편다. 편집·선택은 그동안 막는다
  // (읽는 것과 고치는 것을 섞으면 어느 원고를 고치는지 알 수 없다).
  const shownContent = peek ? peek.content : (data?.content ?? "");
  // 읽는 중에는 고칠 수 없다 - 눈앞의 글이 저장 대상이 아니기 때문이다.
  const canEdit = editable && peek === null;
  const blocks = useMemo(() => splitBlocks(shownContent), [shownContent]);
  // 자료를 빼면서 근거 표기를 잃은 블록. 서버가 마커가 지워지는 순간 문단을 기록해 둔다
  // (지워진 마커는 흔적이 없어 나중에는 알 수 없다). 절 전체 재작성은 실측 $0.4~$1.3인데
  // 이 블록만 고치면 1콜이라 십수 배 싸다 - 어디를 고치면 되는지 짚어 주는 게 핵심이다.
  const lostByBlock = useMemo(() => {
    const marks = (data?.evidence_lost ?? []).filter((m) => m.text.trim());
    if (marks.length === 0) return blocks.map(() => 0);
    return blocks.map((b) =>
      marks.reduce((n, m) => (b.includes(m.text) ? n + (m.n_markers || 1) : n), 0),
    );
  }, [blocks, data]);
  // 문서 순서로 정렬 - 재작성은 위에서 아래로 처리해야 결과가 예측 가능하다.
  const selectedBlocks = useMemo(
    () =>
      [...selectedIdx]
        .filter((i) => i < blocks.length)
        .sort((a, b) => a - b)
        .map((i) => blocks[i]),
    [selectedIdx, blocks],
  );
  const singleBlock = selectedBlocks.length === 1 ? selectedBlocks[0] : null;
  // 근거 드로어 - 선택과 분리된 별도 상태. 블록 선택(재작성 대상 고르기)만으로 열면
  // 근거 0건 블록에서도 빈 패널이 떠 불편하다(2026-08-12 사용자 지적). 근거 있는
  // 블록의 '근거 N' 표시를 사용자가 클릭했을 때만 그 블록의 근거를 연다.
  const [evidenceIdx, setEvidenceIdx] = useState<number | null>(null);
  // 근거 표기 없는 문장은 늘 약한 점선으로 표시된다(MarkdownContent) - 노란 배경을
  // 토글로 켜던 종전 방식을 대체한다(2026-08-26 결정). 약한 신호라 늘 켜 둬도
  // 화면이 덮이지 않고, 문장에 마우스를 올리면 근거까지 그 자리에서 보인다.
  // 두 층을 따로 끄고 켠다 - AI 서술(주황)은 기본 켬, 인용 문장(파랑)은 기본 끔.
  // 켜 두면 "자료를 보고 쓴 글 / AI 서술"이 한눈에 갈리고, 꺼도 마우스를 올리면 보인다.
  const [markUncited, setMarkUncited] = useState(true);
  const [markCited, setMarkCited] = useState(false);
  // 셋째 층 - 어떤 검사도 보지 않은 줄(개조식 명사 종결 등). 기본 끔: 늘 켜 두면
  // 개조식이 많은 절이 온통 밑줄이 된다. "왜 이 줄만 표시가 없지"를 물을 때 켜는 스위치.
  const evidenceBlock = evidenceIdx !== null ? (blocks[evidenceIdx] ?? null) : null;
  // 드로어 2면 중 원문 뷰어 - 근거 카드·수치에서 "원문에서 보기"를 눌렀을 때만.
  // 근거 목록으로 돌아가는 뒤로가기가 있으므로 블록이 바뀌면 반드시 비운다.
  const [sourceView, setSourceView] = useState<SourceLocation | null>(null);
  // 블록별 '이 블록의 근거' 수 + 강한 경고 수 - 표시(배지)의 원천. 드로어와 같은 분류.
  // 경고는 못 찾음·근거 없는 수치만 센다 - 추정·외국어·표기 없음까지 물들이면 문장의
  // 70~90%가 비확정(검증런 실측)이라 모든 블록이 경고색이 되고 신호가 죽는다.
  const evidenceCounts = useMemo(() => {
    const data = evidenceQuery.data;
    if (!data) return [] as { count: number; alert: number; needsCheck: number; defect: number }[];
    return blocks.map((block) => {
      const { primary, claims } = partitionBlockEvidence([block], data);
      const alert = claims.filter(
        (c) => c.status === "unmatched" || c.ungrounded.length > 0,
      ).length;
      // 문장 밑줄과 같은 함수로 가른다 - 배지와 본문이 다른 기준을 쓰면 "확인 필요 2"인데
      // 물결이 3개인 어긋남이 난다.
      const tones = claims.map(claimTone);
      // 인용은 했는데 대목까지는 못 간 문장 - "근거 있음"과 "근거는 있는데 확인이 필요함"을
      // 가르는 값이다. 배지가 둘을 같은 모양으로 그리면 사람이 어디를 열지 못 고른다.
      const needsCheck = tones.filter((t) => t === "unconfirmed").length;
      // 검사에서 걸린 문장 - 미확정과 같은 색이면 "볼 만한 것"과 "틀린 것"이 섞인다.
      const defect = tones.filter((t) => t === "defect").length;
      return { count: primary.length, alert, needsCheck, defect };
    });
  }, [blocks, evidenceQuery.data]);
  // 절 단위 대조 요약 - 블록을 일일이 열지 않아도 어디를 볼지 가늠하게(2026-08-14 지적).
  const claimStats = useMemo(() => {
    const claims = evidenceQuery.data?.claims;
    if (!claims || claims.length === 0) return null;
    // 판정 5종(대목 특정·추정·못 찾음·외국어·표기 없음)을 그대로 늘어놓으면 읽는 사람이
    // 할 일이 안 보인다 - weak·unmatched·crosslingual의 차이는 검출기 사정이라
    // "대목까지 확인됨 / 직접 확인 필요 / 표기 없음" 셋으로 접는다(2026-08-26 화면 정리).
    // 칸 나누기는 본문 밑줄과 **같은 함수**를 쓴다(claimTone) - 종전엔 여기서 status로,
    // 본문에서 마커로 따로 갈라 "대목 확인 2"인데 실선이 3개인 어긋남이 가능했다.
    const tally = { confirmed: 0, unconfirmed: 0, defect: 0, uncited: 0 };
    for (const c of claims) tally[claimTone(c)] += 1;
    return {
      total: claims.length,
      aligned: tally.confirmed,
      needsCheck: tally.unconfirmed,
      uncited: tally.uncited,
      ungrounded: tally.defect,
    };
  }, [evidenceQuery.data]);
  // 색칠에는 합류했지만 무근거 수치·산술 검사에서는 여전히 빠지는 줄 수 - 셈만 알린다.
  const uncoveredCount = evidenceQuery.data?.uncovered?.length ?? 0;
  // 분모 - 화면이 "무엇 중 몇 개"를 말할 수 있게(gate.line_accounting).
  const bodyLines = evidenceQuery.data?.body_lines ?? 0;
  const countedLines = evidenceQuery.data?.counted_lines ?? 0;
  const notCandidate = Math.max(0, bodyLines - countedLines);
  // 드로어가 뜨는 조건과 정확히 같은 식을 부모에 알린다 - 부모는 이때 절 트리를 접어
  // 본문을 넓힌다. 절을 옮기거나 화면을 떠날 때도 반드시 닫힘으로 되돌린다.
  const drawerOpen = evidenceBlock !== null;
  useEffect(() => {
    onEvidenceOpenChange?.(drawerOpen);
    return () => onEvidenceOpenChange?.(false);
  }, [drawerOpen, onEvidenceOpenChange]);
  // 그래프 블록은 AI 재작성에서 뺀다 - 모델이 펜스 안 수치를 고쳐 쓰면 근거와 끊긴다
  // (백엔드도 같은 이유로 막는다). 고칠 게 있으면 표로 되돌린 뒤 고친다.
  const chartSelected = selectedBlocks.some((b) => b.includes("```chart"));
  // 변환 대상 블록은 제목 줄까지 품고 있다(splitBlocks가 표 제목을 표에 붙여 둔다).
  const convertBlock = convertIdx === null ? "" : (blocks[convertIdx] ?? "");
  const convertTable = useMemo<MarkdownTable | null>(
    () => (convertBlock ? findTable(convertBlock) : null),
    [convertBlock],
  );

  if (contentQuery.isLoading) {
    return (
      <div className="p-6">
        <LoadingSkeleton variant="row" count={6} />
      </div>
    );
  }

  if (error) {
    const isNotReady = error instanceof ApiError && error.code === "section_not_ready";
    return (
      <div className="p-6">
        <EmptyState
          icon={isNotReady ? Eye : undefined}
          title={isNotReady ? "아직 작성되지 않은 섹션입니다" : "본문을 불러오지 못했습니다"}
          description={
            isNotReady
              ? "이 섹션은 작성이 완료되는 대로 표시됩니다."
              : error instanceof ApiError
                ? error.message
                : "잠시 후 다시 시도해 주세요."
          }
        />
      </div>
    );
  }

  if (!data) return null;

  const busy = save.isPending || rewrite.isPending || rewriteBlock.isPending;

  const clearBlockSelection = () => {
    setSelectedIdx(new Set());
    setEditingIdx(null);
    setBlockDraft("");
    setInstruction("");
  };

  // 블록 하나를 그 자리에서 편집 시작 - 선택도 그 블록만 남긴다.
  const startBlockEdit = (idx: number) => {
    setSelectedIdx(new Set([idx]));
    setBlockDraft(blocks[idx]);
    setEditingIdx(idx);
  };

  const startEdit = () => {
    setDraft(data.content);
    setEditing(true);
    clearBlockSelection();
  };

  // 오귀속 교정 - 근거 패널의 "출처 n 추가"가 문장의 출처 표기에 번호를 덧붙여 저장한다.
  // 검증은 사람이 원문을 보고 눌렀다는 것 - 자동 교체가 아니라 사람이 승인한 편집이다.
  // 추가·제거 공용 - 문장 치환 저장 + "되돌리기" 토스트. 표기 교정은 한 번에 한
  // 문장이라, 직전 본문을 통째로 쥐고 있으면 되돌리기가 클릭 하나가 된다
  // (2026-08-27 지적: 추가만 있고 제거가 없어 잘못 누른 교정을 못 뺐다).
  const swapClaimAndSave = async (claimText: string, fixedClaim: string, done: string) => {
    const content = data?.content ?? "";
    if (!content.includes(claimText)) {
      toast.error("문장을 본문에서 찾지 못했습니다 - 본문이 수정됐을 수 있습니다.");
      return;
    }
    try {
      await save.mutateAsync(content.replace(claimText, fixedClaim));
      toast.success(done, {
        action: {
          label: "되돌리기",
          onClick: () => {
            save
              .mutateAsync(content)
              .then(() => toast.success("되돌렸습니다."))
              .catch(() => toast.error("되돌리기 실패 - 버전 기록에서 복원하세요."));
          },
        },
      });
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "저장에 실패했습니다.";
      toast.error("저장 실패", { description: msg });
    }
  };

  const onFixCitation = async (claimText: string, number: number) => {
    const fixedClaim = appendSourceNumber(claimText, number);
    if (!fixedClaim) {
      toast.error("이 문장에는 출처 표기가 없거나 이미 그 번호가 있습니다.");
      return;
    }
    await swapClaimAndSave(claimText, fixedClaim, `출처 ${number}을(를) 추가했습니다.`);
  };

  const onRemoveCitation = async (claimText: string, number: number) => {
    const fixedClaim = removeSourceNumber(claimText, number);
    if (fixedClaim === null) {
      toast.error("이 문장의 출처 표기에서 그 번호를 찾지 못했습니다.");
      return;
    }
    await swapClaimAndSave(claimText, fixedClaim, `출처 ${number} 표기를 뺐습니다.`);
  };

  // 주입 의심 조치 - 문제 수치가 든 문장을 그 블록 안에서 국소 재작성한다.
  // 표기 교정(출처 추가)과 달리 값 자체가 의심이라 모델이 근거를 다시 보고 고쳐야 한다.
  const onRewriteSentence = async (claimText: string, token: string) => {
    const block = blocks.find((b) => b.includes(claimText));
    if (!block) {
      toast.error("문장을 본문에서 찾지 못했습니다 - 본문이 수정됐을 수 있습니다.");
      return;
    }
    try {
      await rewriteBlock.mutateAsync({
        block,
        instruction:
          `이 블록에서 "${token}" 수치는 인용한 자료에서 확인되지 않는다. ` +
          `해당 수치를 근거 자료에 실재하는 값으로 교체하고 출처를 표기하라. ` +
          `근거에 대응하는 값이 없으면 그 수치 주장 문장을 제거하되 나머지 서술은 유지하라.`,
      });
      toast.success("문장을 다시 썼습니다 - 결과를 확인하세요.");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "재작성에 실패했습니다.";
      toast.error("재작성 실패", { description: msg });
    }
  };

  const onSave = async () => {
    try {
      await save.mutateAsync(draft);
      toast.success("섹션이 저장됐습니다.");
      setEditing(false);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "저장에 실패했습니다.";
      toast.error("저장 실패", { description: msg });
    }
  };

  // 클릭 = 토글(다중 선택). 직접 편집 중이면 대상이 바뀌지 않게 잠근다.
  const toggleBlock = (idx: number) => {
    if (!canEdit || editing || editingIdx !== null) return;
    setSelectedIdx((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  const onSaveBlock = async () => {
    const target = editingIdx !== null ? blocks[editingIdx] : singleBlock;
    if (!target) return;
    // 선택 블록만 원문에서 치환 - replace의 $패턴 해석을 피하려고 함수 치환을 쓴다
    const next = data.content.replace(target, () => blockDraft);
    try {
      await save.mutateAsync(next);
      toast.success("블록이 저장됐습니다.");
      clearBlockSelection();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "저장에 실패했습니다.";
      toast.error("저장 실패", { description: msg });
    }
  };

  // 선택한 블록들을 문서 순서대로 하나씩 재작성 - 백엔드가 블록 단위 치환이라
  // 순차 처리해야 앞선 결과가 뒤 블록의 원문 매칭을 깨지 않는다(병렬 금지).
  const onRewriteBlocks = async () => {
    if (selectedBlocks.length === 0) return;
    const failed: string[] = [];
    let ok = 0;
    for (const block of selectedBlocks) {
      try {
        await rewriteBlock.mutateAsync({ block, instruction: instruction.trim() });
        ok += 1;
      } catch (err) {
        failed.push(err instanceof ApiError ? err.message : "재작성 실패");
      }
    }
    if (ok > 0) toast.success(`블록 ${ok}개를 다시 작성했습니다.`);
    if (failed.length > 0)
      toast.error(`${failed.length}개 실패`, { description: failed.join(" · ") });
    clearBlockSelection();
  };

  // 원문 조각 하나를 다른 마크다운으로 갈아 끼우고 저장한다(표↔그래프 교환의 공통 경로).
  const replaceBlock = async (target: string, next: string, done: string) => {
    if (!target) return;
    try {
      await save.mutateAsync(data.content.replace(target, () => next));
      toast.success(done);
      clearBlockSelection();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "저장에 실패했습니다.";
      toast.error("저장 실패", { description: msg });
    }
  };

  // 그래프를 표로 되돌린다 - 변환할 때 펜스 안에 넣어 둔 원본 표를 그대로 꺼낸다.
  const onRevertToTable = async (idx: number) => {
    const table = chartFallbackTable(blocks[idx] ?? "");
    if (!table) {
      toast.error("되돌릴 표가 없습니다", {
        description: "원본 표가 보관되지 않은 그래프입니다 - 블록 편집으로 고쳐 주세요.",
      });
      return;
    }
    await replaceBlock(blocks[idx], table, "표로 되돌렸습니다.");
  };

  const onRewriteSection = async () => {
    try {
      await rewrite.mutateAsync(instruction.trim());
      toast.success("AI가 이 섹션을 다시 작성했습니다.");
      clearBlockSelection();
      setEditing(false);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "재작성에 실패했습니다.";
      toast.error("재작성 실패", { description: msg });
    }
  };

  return (
    <article className="flex flex-col">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-6 py-4">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold text-fg">{data.title}</h2>
          {/* 장 화면(SectionBlock)과 같은 배지 - 절 상세만 안 보이던 불일치 수리(2026-08-27) */}
          {data.evidence.scarce ? (
            <Badge
              variant="secondary"
              className="bg-bg-warning text-fg-warning"
              title="확보된 근거가 적어 분량을 줄여 작성한 절입니다"
            >
              자료 부족
            </Badge>
          ) : null}
          {data.qa_status === "passed" ? (
            <Badge variant="default" className="bg-bg-success text-fg-success">
              QA 통과
            </Badge>
          ) : data.qa_status === "failed" ? (
            // 소프트 경고(분량·수치 근거 등)는 차단이 아니라 참고 - '실패'는 과한 라벨이라
            // 오해를 부른다(2026-08-09 지적). 경고로 표기하고 상세는 PM 배너가 보여준다.
            <Badge variant="outline" className="border-fg-warning/40 bg-bg-warning text-fg">
              QA 경고
            </Badge>
          ) : (
            <Badge variant="secondary">QA 대기</Badge>
          )}
        </div>
        <div className="flex items-center gap-2">
          {/* 이 절의 이력 - 되돌리기는 읽기 전용에서도 '보는 것'까지는 된다.
              버전 기록 카드로 가서 비교를 열고 이 절을 찾아 내려가던 길을 없앤다. */}
          <Button
            variant="ghost"
            size="sm"
            aria-pressed={historyOpen}
            onClick={() => setHistoryOpen((v) => !v)}
            title="이 절이 예전에 어땠는지 보고, 그때 내용으로 되돌립니다"
          >
            <History className="mr-1 h-4 w-4" />
            이력
          </Button>
          {/* 자물쇠 - 켜면 이 절에 AI가 못 닿는다. 묶음 재작성이 수십 절을 한 번에
              갈아엎는 자리라(전체 실측 $15.5), 공들여 손본 절을 지키는 유일한 수단이다.
              사람의 직접 편집은 막지 않는다 - 잠근 사람이 그 사람이다. */}
          {canEdit || data.locked ? (
            <Button
              variant={data.locked ? "outline" : "ghost"}
              size="sm"
              disabled={setLock.isPending}
              aria-pressed={data.locked}
              title={
                data.locked
                  ? "AI 재작성이 막혀 있습니다 - 눌러서 풉니다"
                  : "이 절을 AI 재작성에서 제외합니다(직접 편집은 그대로)"
              }
              onClick={() =>
                setLock.mutate(!data.locked, {
                  onSuccess: (res) =>
                    toast.success(res.locked ? "이 절을 잠갔습니다" : "잠금을 풀었습니다", {
                      description: res.locked
                        ? "AI 재작성에서 제외됩니다. 직접 편집은 그대로 됩니다."
                        : "이제 AI가 다시 쓸 수 있습니다.",
                    }),
                  onError: (err: unknown) =>
                    toast.error("잠금 변경 실패", {
                      description: err instanceof ApiError ? err.message : undefined,
                    }),
                })
              }
            >
              {data.locked ? (
                <Lock className="mr-1 h-4 w-4" />
              ) : (
                <LockOpen className="mr-1 h-4 w-4" />
              )}
              {data.locked ? "잠김" : "잠그기"}
            </Button>
          ) : null}
          {editing ? (
            <>
              <Button variant="ghost" size="sm" onClick={() => setEditing(false)} disabled={busy}>
                <X className="mr-1 h-4 w-4" />
                취소
              </Button>
              <Button size="sm" onClick={() => void onSave()} disabled={busy}>
                <Save className="mr-1 h-4 w-4" />
                {save.isPending ? "저장 중…" : "저장"}
              </Button>
            </>
          ) : canEdit ? (
            // 블록별 인라인 편집이 기본 경로라 전체 편집은 ghost로 낮춘다
            // (긴 본문을 한 상자에 넣으면 쓰기 불편하다는 실사용 지적, 2026-08-09)
            <Button variant="ghost" size="sm" onClick={startEdit} disabled={busy}>
              <Pencil className="mr-1 h-4 w-4" />절 전체 편집
            </Button>
          ) : (
            // 작성 중에는 편집 진입점을 두지 않는다 - 초안이 재생성으로 갈아치워질 수
            // 있고, 절 전체 재작성은 작성 루프와 검색 자원을 다툰다(사용자 결정).
            <Badge variant="secondary">작성 중 · 읽기 전용</Badge>
          )}
        </div>
      </header>

      {peek ? (
        <div className="flex flex-wrap items-center gap-2 border-b border-fg-info/30 bg-bg-info px-6 py-2.5 text-xs text-fg">
          <Eye className="h-3.5 w-3.5 shrink-0 text-fg-info" aria-hidden />
          <span className="font-medium">
            v{peek.version_no}
            {peek.until_version !== peek.version_no ? `~v${peek.until_version}` : ""} 시점의 원고를
            보는 중
          </span>
          <span className="text-fg-secondary">
            읽기 전용입니다 - 본문은 그대로이고, 되돌리려면 아래 이력에서 '이 내용으로'를 누르세요.
          </span>
          <Button
            variant="outline"
            size="sm"
            className="ml-auto h-7 px-2 text-xs"
            onClick={() => setPeek(null)}
          >
            <X className="mr-1 h-3.5 w-3.5" aria-hidden />
            지금 본문으로
          </Button>
        </div>
      ) : null}
      {historyOpen ? (
        <SectionHistoryPanel
          projectId={projectId}
          sectionId={sectionId}
          current={data.content}
          editable={editable}
          onPeek={setPeek}
        />
      ) : null}

      {data.evidence.plan_failed ? (
        // 분할 계획이 무너지면 절이 단일 호출로 떨어져 짧아지고 인용이 준다.
        // 지금까지 아무 신호가 없어 그 절이 조용히 보고서에 실렸다(2026-08-11).
        <div className="flex items-center gap-1.5 border-b border-fg-warning/30 bg-bg-warning px-6 py-2.5">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-fg-warning" aria-hidden />
          <p className="text-xs text-fg">
            분할 계획 실패 - 이 절은 한 번에 작성돼 분량이 짧고 근거 인용이 얇을 수 있습니다.
            재작성하면 정상 분할로 다시 씁니다.
          </p>
        </div>
      ) : null}

      {qaWarnings.length > 0 ? (
        <div className="flex flex-col gap-1.5 border-b border-fg-warning/30 bg-bg-warning px-6 py-3">
          <p className="text-xs font-medium text-fg">정적검사 경고 {qaWarnings.length}건</p>
          <ul className="flex flex-col gap-1">
            {/* 정적검사는 check당 결과 1건 - check 이름이 곧 고유 키다 */}
            {qaWarnings.map((w) => (
              <li key={w.check} className="flex items-start gap-1.5 text-xs text-fg-secondary">
                <Badge
                  variant="outline"
                  className="shrink-0 border-fg-warning/40 bg-bg-warning font-normal"
                >
                  {WARNING_LABEL[w.check] ?? w.check}
                </Badge>
                {w.detail ? <span className="pt-0.5">{w.detail}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {editing ? (
        <div className="px-6 py-4">
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="min-h-[420px] font-mono text-sm"
            aria-label="섹션 본문 편집"
          />
        </div>
      ) : (
        <div>
          {/* 근거는 그리드 칸이 아니라 화면 오른쪽 드로어로 연다. 칸으로 두면 근거가
              0건인 절에서도 본문이 늘 좁아지고, 스크롤이 본문과 엮여 긴 근거를 읽는
              동안 본문 위치를 잃는다(2026-08-12 지적).

              본문에 오른쪽 여백 보정은 주지 않는다 - 드로어 폭만큼 밀면 본문이 절반으로
              줄어든다(2026-08-12 사용자 지적: 사이드에서 열릴 거면 본문을 축소하지 말 것).
              드로어는 사용자가 '근거 N' 표시를 눌렀을 때만 뜨므로, 열려 있는 동안 본문
              오른쪽 일부가 가려지는 것은 감수한다(부모가 절 트리를 접어 본문을 왼쪽으로
              넓혀 주는 보정만 유지). */}
          <div className="min-w-0 px-6 py-4">
            {(rewrite.isPending || rewriteBlock.isPending) && (
              <div className="mb-3 flex items-center gap-2 rounded border border-border-info bg-bg-info px-3 py-2 text-xs text-fg-info">
                <Sparkles className="h-4 w-4 animate-pulse" />
                AI가 {rewriteBlock.isPending ? "선택한 블록을" : "이 섹션을"} 다시 작성하고
                있습니다…
              </div>
            )}
            {blocks.length === 0 && !rewrite.isPending ? (
              // 작성 실패로 빈 절 - 본문이 없으니 블록 편집 대신 재작성 진입점을 바로 준다
              // (2026-08-14 지적: 빈 절에서 재작성 버튼이 없어 복구 경로가 막혔다).
              <EmptyState
                icon={Sparkles}
                title="이 절은 작성되지 않았습니다"
                description="작성 단계에서 실패했거나 비어 있는 절입니다. 다시 쓰면 프로젝트 자료에서 근거를 검색해 처음부터 새로 작성합니다."
                action={
                  editable ? (
                    <Button onClick={() => void onRewriteSection()} disabled={busy}>
                      <Sparkles className="mr-1 h-4 w-4" />
                      AI로 이 절 작성
                    </Button>
                  ) : undefined
                }
              />
            ) : null}
            {/* 절 단위 대조 요약 - 블록을 일일이 열지 않아도 이 절의 근거 상태를 한 줄로.
                셈과 스위치를 갈라 놓는다: 위는 "이 절이 어떤 상태인가"(읽기), 아래는
                "본문에 무엇을 칠할까"(조작). 종전엔 둘이 섞여 서술형 문구와 버튼이
                번갈아 나와 어느 것이 눌리는지도 안 보였다(2026-08-27 지적).
                용어는 물음표에 모아 둔다 - 줄에 설명을 붙이면 다시 길어진다. */}
            {EVIDENCE_UI_ENABLED && (claimStats || uncoveredCount > 0) ? (
              <div className="mb-2 flex flex-col gap-1.5 text-sm">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-fg-secondary">
                  <span className="font-medium text-fg">근거 대조</span>
                  <HelpTip title="이 셈이 뜻하는 것">
                    <p>
                      <b>문장</b> - 근거 대조 대상으로 잡힌 본문 문장. 제목·표·캡션과 너무 짧은 줄은
                      빠집니다.
                    </p>
                    <p>
                      본문 밑줄은 두 가지를 한꺼번에 말합니다 - <b>색</b>은 인용 표기를 달았는지
                      (파랑 있음 / 주황 없음), <b>선 모양</b>은 그 표기가 실제로 받치는지입니다.
                    </p>
                    <p>
                      <b>대목 확인</b> (파랑 실선) - 인용한 자료에서 그 문장을 받치는 대목까지 찾은
                      것. 문장에 마우스를 올리면 그 대목이 뜹니다.
                    </p>
                    <p>
                      <b>확인 필요</b> (파랑 점선) - 인용 표기는 있는데 대목을 못 집은 것. 근거가
                      외국어면 겹침으로 잴 수 없어 여기 들어갑니다 - 틀렸다는 뜻이 아니라 사람이
                      원문을 봐야 한다는 뜻입니다.
                    </p>
                    <p>
                      <b>무근거 수치</b> (빨강 물결) - 인용한 자료에서 못 찾은 수치가 든 문장.
                      지어냈거나 다른 자료에서 왔을 수 있습니다. 없는 게 아니라{" "}
                      <b>검사에서 걸린 것</b>이라 표시 스위치와 무관하게 늘 보입니다.
                    </p>
                    <p>
                      <b>표기 없음</b> (주황 점선) - 인용 표기 없이 쓰인 문장. AI가 자료 없이 쓴
                      서술일 수 있습니다.
                    </p>
                    <p>
                      <b>검사 제외</b> - 문장 종결형이 아니고 수치도 없어 무근거 수치·산술 검사에서
                      빠진 줄. 밑줄은 마커 유무로 칠해져 있습니다.
                    </p>
                    <p>
                      <b>대상 아님</b> - 애초에 대조 대상으로 안 잡는 줄입니다. 표 안의 행, 표·그림
                      제목, "(단위: 억 원)" 같은 표기 줄, 장·절 제목이 여기 해당합니다. 서술이
                      아니라 구조물이라 근거 대조가 성립하지 않습니다. 본문 줄 수에서 대조한 줄 수를
                      뺀 나머지가 이 숫자입니다.
                    </p>
                  </HelpTip>
                  {claimStats ? (
                    <>
                      {/* 분모를 맨 앞에 세운다 - "86개 중 2개 확인"은 86이 무엇인지
                          모르면 좋은 비율인지 알 수 없다. 실제 본문은 122줄인데 86만
                          대조 대상이었다면 2/86이 아니라 2/122가 실상에 가깝다
                          (2026-08-27 지적). 숫자가 닫히게 대상 아님까지 함께 보인다. */}
                      {bodyLines > 0 ? (
                        <span>
                          본문 {bodyLines}줄 중 {countedLines}줄을 대조
                        </span>
                      ) : (
                        <span>문장 {claimStats.total}</span>
                      )}
                      <span aria-hidden>—</span>
                      <span className={claimStats.aligned > 0 ? "text-fg-success" : undefined}>
                        대목 확인 {claimStats.aligned}
                      </span>
                      {claimStats.needsCheck > 0 ? (
                        <>
                          <span aria-hidden>·</span>
                          <span className="text-fg-warning">확인 필요 {claimStats.needsCheck}</span>
                        </>
                      ) : null}
                      {claimStats.ungrounded > 0 ? (
                        <>
                          <span aria-hidden>·</span>
                          {/* 없음이 아니라 **걸린 것**이라 셈에도 세운다 - 물음표 안에만
                              두면 실제로 결함이 있는 절과 없는 절이 같아 보인다. */}
                          <span className="text-fg-danger">
                            무근거 수치 {claimStats.ungrounded}
                          </span>
                        </>
                      ) : null}
                      {claimStats.uncited > 0 ? (
                        <>
                          <span aria-hidden>·</span>
                          <span className="text-fg-warning">표기 없음 {claimStats.uncited}</span>
                        </>
                      ) : null}
                    </>
                  ) : (
                    // 주장이 하나도 안 잡힌 절 - 여기서 줄을 통째로 감추면 "왜 표시가
                    // 없지"에 답할 자리가 사라진다. 이 기능이 풀려던 문제가 정확히 그것이다.
                    <span>대조 대상으로 잡힌 문장이 없습니다</span>
                  )}
                  {uncoveredCount > 0 ? (
                    <>
                      <span aria-hidden>·</span>
                      <span>검사 제외 {uncoveredCount}</span>
                    </>
                  ) : null}
                  {notCandidate > 0 ? (
                    <>
                      <span aria-hidden>·</span>
                      <span className="text-fg-tertiary">대상 아님 {notCandidate}</span>
                    </>
                  ) : null}
                  {evidenceQuery.data?.traceable === false ? (
                    // 근거 기록 도입(8/11) 전에 작성된 절 - 번호와 청크의 대응이 없어
                    // 블록 배지·드로어를 못 연다. 그 사실을 알려야 "왜 배지가 없지"가 안 된다.
                    <span className="text-fg-tertiary">
                      (근거 기록이 없는 옛 작성분 - 블록별 근거 보기는 새로 작성한 절부터)
                    </span>
                  ) : null}
                </div>
                {/* 본문 밑줄 스위치 - 셈과 갈라 아래 줄에 둔다. 테두리를 줘 스위치로
                    보이게 하고, 켜짐은 각 층의 색으로 남긴다. 꺼도 마우스를 올리면
                    밑줄과 카드가 뜬다(표시는 훑기용, 호버는 확인용). */}
                {claimStats ? (
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="text-fg-tertiary">본문 표시</span>
                    <button
                      type="button"
                      onClick={() => setMarkUncited((v) => !v)}
                      aria-pressed={markUncited}
                      title="근거 표기 없이 쓰인 문장(AI 서술)에 주황 밑줄을 그립니다"
                      className={cn(
                        "rounded border px-2 py-0.5 transition-colors",
                        markUncited
                          ? "border-fg-warning bg-bg-warning text-fg"
                          : "border-border text-fg-secondary hover:border-border-strong",
                      )}
                    >
                      AI 서술 {markUncited ? "켬" : "끔"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setMarkCited((v) => !v)}
                      aria-pressed={markCited}
                      title="자료를 인용해 쓴 문장에 파란 밑줄을 그립니다"
                      className={cn(
                        "rounded border px-2 py-0.5 transition-colors",
                        markCited
                          ? "border-fg-info bg-bg-info text-fg-info"
                          : "border-border text-fg-secondary hover:border-border-strong",
                      )}
                    >
                      인용 문장 {markCited ? "켬" : "끔"}
                    </button>
                  </div>
                ) : null}
              </div>
            ) : null}
            {/* 뽑아 둔 안 - 바로 아래가 현재 본문이라 그 자리에서 견준다. 스크롤을 함께
                타므로 얼마든 길어져도 되고, 하단 고정 바를 부풀리지 않는다. */}
            <SectionVariantsPanel
              projectId={projectId}
              sectionId={sectionId}
              currentContent={data?.content ?? ""}
            />
            <div className="flex flex-col gap-1">
              {blocks.map((block, idx) => (
                // biome-ignore lint/a11y/useSemanticElements: 블록 안에 인용 링크(<a>)가 렌더돼 <button> 중첩은 invalid HTML - div+role/키핸들러로 대체
                <div
                  // biome-ignore lint/suspicious/noArrayIndexKey: 블록 정체성 = 본문 내 위치(내용 중복 가능·재정렬 없음)
                  key={`block-${idx}`}
                  role="button"
                  tabIndex={0}
                  aria-pressed={selectedIdx.has(idx)}
                  aria-label={`블록 ${idx + 1} 선택`}
                  onClick={() => toggleBlock(idx)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      toggleBlock(idx);
                    }
                  }}
                  className={cn(
                    "group relative cursor-pointer rounded border px-3 py-1.5 transition-colors",
                    // 선택 블록은 강조 배경 + 좌측 굵은 띠로 한눈에 구분(다중 선택 전제)
                    selectedIdx.has(idx)
                      ? "border-accent bg-bg-info ring-1 ring-accent/40 pl-4 before:absolute before:left-0 before:top-0 before:h-full before:w-1 before:rounded-l before:bg-accent"
                      : "border-transparent hover:border-border hover:bg-bg-secondary",
                  )}
                >
                  {editingIdx === idx ? (
                    // biome-ignore lint/a11y/noStaticElementInteractions: 편집 중 부모(블록 선택)로 클릭이 새지 않게 막는 껍데기 - 그 자체는 조작 대상이 아니다
                    // biome-ignore lint/a11y/useKeyWithClickEvents: 키보드로는 부모 선택이 발동하지 않아 막을 것이 없다
                    <div className="flex flex-col gap-2" onClick={(e) => e.stopPropagation()}>
                      <Textarea
                        value={blockDraft}
                        onChange={(e) => setBlockDraft(e.target.value)}
                        className="min-h-[160px] font-mono text-xs"
                        aria-label="블록 편집"
                      />
                      <div className="flex items-center gap-2">
                        <Button size="sm" onClick={() => void onSaveBlock()} disabled={busy}>
                          <Save className="mr-1 h-3.5 w-3.5" />
                          {save.isPending ? "저장 중…" : "저장"}
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setEditingIdx(null)}
                          disabled={busy}
                        >
                          취소
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <>
                      {/* 그래프로 바꿀 수 있는 표라고 먼저 알린다. 변환 버튼은 블록을 하나만
                          고른 뒤에야 나와서, 표가 그래프가 될 수 있다는 걸 알 방법이 없었다
                          (2026-08-28). 작성 직후 자동 변환은 확신할 때만 바꾸므로 남은 표
                          중에도 사람이 보면 바꿀 만한 것이 있다. 마우스를 얹거나 키보드로
                          닿을 때만 떠 본문을 어지르지 않는다. */}
                      {editable && chartableTable(block) !== null ? (
                        <button
                          type="button"
                          title="이 표를 그래프로 바꿉니다 - 표의 수치를 그대로 쓰고 원본 표는 안에 보관됩니다"
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelectedIdx(new Set([idx]));
                            setConvertIdx(idx);
                          }}
                          onKeyDown={(e) => e.stopPropagation()}
                          // 자리는 블록 왼쪽 아래다. 오른쪽 위에는 근거 배지가 이미 둘
                          // 있고(근거 수·근거 없음), 같은 자리에 두면 뒤에 깔려 안 보인다 -
                          // opacity는 1인데 화면에는 없었다(2026-08-28 CDP 확인).
                          className="absolute -bottom-2 left-2 z-10 inline-flex items-center gap-1 rounded-full border border-border bg-bg px-1.5 py-0.5 text-[10px] text-fg-secondary opacity-0 transition-opacity hover:border-border-strong hover:text-fg focus-visible:opacity-100 group-hover:opacity-100"
                        >
                          <BarChart3 className="h-3 w-3" aria-hidden />
                          그래프로
                        </button>
                      ) : null}
                      {/* 근거 있는 블록에만 표시를 달고, 눌렀을 때만 드로어를 연다 -
                          근거 0건 블록에서 빈 패널이 뜨는 불편을 없앤다(2026-08-12).
                          강한 경고(못 찾음·근거 없는 수치)가 있는 블록은 배지를 경고색으로 -
                          블록을 열지 않고도 어디를 볼지 훑을 수 있게(2026-08-14 지적). */}
                      {(lostByBlock[idx] ?? 0) > 0 ? (
                        // 눌러 그 블록을 고른다 - 아래 재작성 바가 그대로 대상이 된다.
                        // 새 버튼을 또 만들지 않는 이유: 고르기→지시→실행이 이미 한 줄이다.
                        <button
                          type="button"
                          title="자료를 빼면서 이 블록의 근거 표기가 사라졌습니다 - 눌러 고른 뒤 이 블록만 다시 쓰면 됩니다"
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelectedIdx(new Set([idx]));
                          }}
                          onKeyDown={(e) => e.stopPropagation()}
                          className="absolute -top-2 left-2 z-10 inline-flex items-center gap-1 rounded-full border border-fg-warning/50 bg-bg-warning px-1.5 py-0.5 text-[10px] text-fg hover:border-fg-warning"
                        >
                          <AlertTriangle className="h-3 w-3" aria-hidden />
                          근거 빠짐 {lostByBlock[idx]}
                        </button>
                      ) : null}
                      {/* 근거가 **로드된 뒤에만** 없다고 말한다. 종전엔 배지 조건이
                          `count > 0`이라 로딩·실패 때 조용히 안 그렸는데, '근거 없음'을
                          추가하면서 `?? 0`이 로딩 중과 0건을 같은 값으로 만들었다 -
                          응답이 늦거나 실패하면 **모든 블록이 "근거 없음"**이 된다
                          (2026-08-27 실사고: COMPA에서 전 블록이 그렇게 보였다).
                          정보 없음을 정보 있음처럼 표시하는 그 계급이라 조건을 분리한다. */}
                      {EVIDENCE_UI_ENABLED &&
                      evidenceQuery.data &&
                      (evidenceCounts[idx]?.count ?? 0) === 0 ? (
                        // 인용이 하나도 없는 블록 - 종전엔 배지를 아예 안 그려서 "근거 없이
                        // 쓰인 블록"이 **부재**로만 나타났다. 규칙을 아는 사람만 읽을 수 있는
                        // 신호라 훑을 때 놓친다(2026-08-26 지적). 부재를 존재로 바꾼다.
                        // 경고색은 쓰지 않는다 - 개조식 보고서엔 표기 없는 블록이 흔하고,
                        // 다 붉히면 진짜 경고(못 찾음·근거 없는 수치)가 묻힌다.
                        <span
                          title="이 블록에는 인용 표기가 없습니다 - 근거 없이 쓰였을 수 있습니다"
                          className="absolute -top-2 right-2 z-10 inline-flex items-center gap-1 rounded-full border border-dashed border-border bg-bg px-1.5 py-0.5 text-[10px] text-fg-tertiary"
                        >
                          근거 없음
                        </span>
                      ) : null}
                      {EVIDENCE_UI_ENABLED && (evidenceCounts[idx]?.count ?? 0) > 0 ? (
                        <button
                          type="button"
                          title={
                            (evidenceCounts[idx]?.defect ?? 0) > 0
                              ? "인용한 자료에서 못 찾은 수치가 있습니다 - 눌러서 확인하세요"
                              : (evidenceCounts[idx]?.needsCheck ?? 0) > 0
                                ? "인용은 했지만 대목까지 확인되지 않은 문장이 있습니다 - 눌러서 확인하세요"
                                : "이 블록의 문장이 어느 자료의 어느 대목을 보고 쓰였는지 봅니다"
                          }
                          onClick={(e) => {
                            e.stopPropagation();
                            setEvidenceIdx((v) => (v === idx ? null : idx));
                            setSourceView(null);
                          }}
                          onKeyDown={(e) => e.stopPropagation()}
                          className={cn(
                            "absolute -top-2 right-2 z-10 inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] transition-colors",
                            // 네 상태를 색으로 가른다: 열림 · 걸린 것 · 확인 필요 · 전부
                            // 확인됨. 종전엔 "걸린 것"과 "확인 필요"가 같은 주황이라
                            // **틀린 블록과 아직 안 본 블록이 같아 보였다** - 앞은 고쳐야
                            // 하고 뒤는 읽어 보면 되는 것이라 할 일이 다르다(2026-08-27).
                            evidenceIdx === idx
                              ? "border-accent bg-bg-info text-fg-info"
                              : (evidenceCounts[idx]?.defect ?? 0) > 0
                                ? "border-fg-danger/50 bg-bg-danger text-fg-danger hover:border-fg-danger"
                                : (evidenceCounts[idx]?.needsCheck ?? 0) > 0
                                  ? "border-fg-warning/50 bg-bg-warning text-fg hover:border-fg-warning"
                                  : "border-fg-success/40 bg-bg-success text-fg-success hover:border-fg-success",
                          )}
                        >
                          <FileSearch className="h-3 w-3" aria-hidden />
                          근거 {evidenceCounts[idx]?.count}
                          {(evidenceCounts[idx]?.defect ?? 0) > 0
                            ? ` · 무근거 수치 ${evidenceCounts[idx]?.defect}`
                            : (evidenceCounts[idx]?.needsCheck ?? 0) > 0
                              ? ` · 확인 필요 ${evidenceCounts[idx]?.needsCheck}`
                              : ""}
                        </button>
                      ) : null}
                      <MarkdownContent
                        content={block}
                        citations={data.citations}
                        evidence={evidenceQuery.data?.items}
                        claims={evidenceQuery.data?.claims}
                        uncovered={evidenceQuery.data?.uncovered}
                        traceable={evidenceQuery.data?.traceable}
                        markCited={markCited}
                        markUncited={markUncited}
                      />
                      {selectedIdx.has(idx) && selectedIdx.size === 1 ? (
                        <div className="mt-1 flex flex-wrap items-center gap-1.5">
                          <Button
                            variant="outline"
                            size="sm"
                            className="h-7 px-2 text-xs"
                            onClick={(e) => {
                              e.stopPropagation();
                              startBlockEdit(idx);
                            }}
                            disabled={busy}
                          >
                            <Pencil className="mr-1 h-3 w-3" />이 블록 편집
                          </Button>
                          {/* 표를 그래프로 바꾸는 것은 사람이 판단한다 - 표를 담은 블록에만 연다 */}
                          {findTable(block) !== null ? (
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-7 px-2 text-xs"
                              onClick={(e) => {
                                e.stopPropagation();
                                setConvertIdx(idx);
                              }}
                              disabled={busy}
                            >
                              <BarChart3 className="mr-1 h-3 w-3" />
                              그래프로 바꾸기
                            </Button>
                          ) : null}
                          {block.includes("```chart") ? (
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-7 px-2 text-xs"
                              onClick={(e) => {
                                e.stopPropagation();
                                void onRevertToTable(idx);
                              }}
                              disabled={busy}
                            >
                              <Table2 className="mr-1 h-3 w-3" />
                              표로 되돌리기
                            </Button>
                          ) : null}
                        </div>
                      ) : null}
                    </>
                  )}
                </div>
              ))}
            </div>
            <CitationList citations={data.citations} />
            {/* 절 단위 근거 패널은 뺐다(2026-08-12 결정). 블록 옆 '근거 N' 배지가 같은
                정보를 고칠 대상 옆에서 보여주므로, 본문 아래 접이식은 같은 것을 두 번
                말하는 자리가 됐다. 출처 목록은 남긴다 - 그건 "이 자료"를 세는 것이고
                배지는 "이 대목"을 가리키는 것이라 층이 다르다. */}
          </div>

          {/* 오른쪽 드로어: 근거 전용. 화면에 고정돼 본문 위로 뜨므로 본문 폭이 안 줄고,
              스크롤도 본문과 따로 논다 - 긴 근거를 읽는 동안 본문 위치를 잃지 않는다.
              블록 선택(재작성)과는 무관하게 '근거 N' 표시 클릭으로만 열고 닫는다. */}
          {EVIDENCE_UI_ENABLED && evidenceBlock !== null ? (
            <aside
              aria-label="선택한 블록의 근거"
              className={cn(
                "fixed inset-y-0 right-0 z-40 flex w-full max-w-[92vw] flex-col border-l border-border bg-bg-secondary shadow-xl",
                // 원문 뷰어는 문서를 통째로 보여주므로 근거 목록보다 폭을 더 준다
                sourceView ? "sm:w-[560px] 2xl:w-[720px]" : "sm:w-[460px] 2xl:w-[560px]",
              )}
            >
              <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
                {sourceView ? (
                  <button
                    type="button"
                    onClick={() => setSourceView(null)}
                    className="inline-flex items-center gap-1 text-xs font-medium text-fg-info hover:underline"
                  >
                    <ChevronLeft className="h-3.5 w-3.5" />
                    근거 목록
                  </button>
                ) : (
                  <p className="text-xs font-medium text-fg">블록 근거</p>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setEvidenceIdx(null);
                    setSourceView(null);
                  }}
                >
                  <X className="h-3.5 w-3.5" />
                  닫기
                </Button>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
                {sourceView ? (
                  <SourceViewer projectId={projectId} location={sourceView} />
                ) : (
                  <BlockEvidence
                    projectId={projectId}
                    sectionId={sectionId}
                    blocks={[evidenceBlock]}
                    onLocate={setSourceView}
                    onFixCitation={canEdit ? onFixCitation : undefined}
                    onRewriteSentence={canEdit ? onRewriteSentence : undefined}
                    onRemoveCitation={canEdit ? onRemoveCitation : undefined}
                  />
                )}
              </div>
            </aside>
          ) : null}
        </div>
      )}
      {editable && !editing ? (
        // 선택 → 지시 → 실행이 가로 한 줄에 놓이도록 하단 띠로 뺐다. 평소에는 화면을
        // 차지하지 않고, 근거 패널이 세로 전체를 쓴다.
        // z-20: 블록의 '근거 N' 배지(absolute z-10)가 스크롤 중 이 바를 뚫고 그려지는
        // 것을 막는다(2026-08-14 사용자 발견). 드로어(z-40)보다는 아래.
        <div className="sticky bottom-0 z-20 flex flex-wrap items-center gap-2 border-t border-border bg-bg px-6 py-3">
          <Label htmlFor="rewrite-instruction" className="text-xs text-fg-secondary">
            {selectedBlocks.length > 0 ? `블록 ${selectedBlocks.length}개` : "절 전체"} 재작성 지시
          </Label>
          {/* 한 줄 지시 입력 - textarea를 한 줄 높이로 누르면 Windows에서 세로 스크롤
              화살표가 스피너처럼 보인다(2026-08-14 사용자 지적). 단일행 Input으로. */}
          <Input
            id="rewrite-instruction"
            type="text"
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            placeholder={
              selectedBlocks.length > 0
                ? "예: 더 간결하게, 수치를 앞세워서"
                : "예: 최신 수치로 갱신하고 근거를 더 달아서"
            }
            className="h-9 flex-1 text-xs"
            disabled={chartSelected}
          />
          <Button
            size="sm"
            variant={selectedBlocks.length > 0 ? "default" : "outline"}
            onClick={() =>
              void (selectedBlocks.length > 0 ? onRewriteBlocks() : onRewriteSection())
            }
            disabled={busy || (selectedBlocks.length > 0 && chartSelected)}
          >
            <Sparkles className="mr-1 h-3.5 w-3.5" />
            {busy
              ? "작성 중…"
              : selectedBlocks.length > 0
                ? `선택 ${selectedBlocks.length}개 재작성`
                : "절 전체 재작성"}
          </Button>
          {/* 값은 **그 버튼 옆에** 붙인다. 예전엔 두 값이 아래 한 줄에 나란히 놓여
              어느 것이 어느 버튼 값인지 알 수 없었다(2026-08-27 지적: "가격이 3개
              있는데 뭐가 맞는지 모르겠다"). 근거(절당 단가·출처)는 아래 한 번만 쓴다. */}
          {selectedBlocks.length === 0 && !busy
            ? (() => {
                const one = estimateLabel(costBasis.data, 1, { compact: true });
                return one ? (
                  <span className="text-[11px] text-fg-tertiary">{one} · 절 1개</span>
                ) : null;
              })()
            : null}
          {/* 눌러 담는 지시 - 빈 칸을 마주하는 시간을 없앤다. 이미 담긴 말은 다시
              담지 않는다(같은 지시를 두 번 쓰면 모델이 그 방향으로 과하게 기운다). */}
          <div className="flex w-full flex-wrap items-center gap-1">
            {(selectedBlocks.length > 0 ? BLOCK_HINTS : SECTION_HINTS).map((hint) => (
              <button
                key={hint}
                type="button"
                disabled={busy || instruction.includes(hint)}
                onClick={() => setInstruction((v) => (v.trim() ? `${v.trim()}, ${hint}` : hint))}
                className={cn(
                  "rounded-full border px-2 py-0.5 text-[11px] transition-colors",
                  instruction.includes(hint)
                    ? "border-accent bg-bg-info text-fg-info"
                    : "border-border text-fg-secondary hover:border-border-strong hover:text-fg",
                )}
              >
                {hint}
              </button>
            ))}
            {instruction.trim() ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => setInstruction("")}
                className="px-1.5 text-[11px] text-fg-tertiary hover:text-fg-secondary"
              >
                지우기
              </button>
            ) : null}
          </div>
          {/* 안 고르기 - 절 전체를 대상으로 할 때만. 블록 하나에 3안은 견줄 거리가
              못 되고 값만 는다. 지시는 위 칸을 그대로 쓴다(지시 칸이 둘이면 어느 쪽이
              먹는지 모른다). */}
          {selectedBlocks.length === 0 ? (
            <SectionVariantsTrigger
              projectId={projectId}
              sectionId={sectionId}
              instruction={instruction}
              disabled={busy || data.locked}
            />
          ) : null}
          {/* 값의 근거는 한 번만 말한다 - 버튼마다 "(이 보고서 실측)"을 되풀이하면
              정작 금액이 안 읽힌다. */}
          {selectedBlocks.length === 0 && !busy && costBasis.data?.per_section_usd != null ? (
            <span className="w-full text-[11px] text-fg-tertiary">
              절당 {formatUsd(costBasis.data.per_section_usd)} 기준 (
              {costBasis.data.basis === "project" ? "이 보고서 실측" : "비슷한 보고서 평균"})
            </span>
          ) : null}
          {/* 설명은 버튼이 스스로 말하게 하고 걷어냈다(2026-08-14 사용자 결정: 입력칸만).
              그래프 차단 안내만 남긴다 - 버튼이 비활성인 이유는 말해줘야 한다. */}
          {chartSelected ? (
            <p className="w-full text-[11px] leading-relaxed text-fg-tertiary">
              그래프 블록은 AI 재작성 대상이 아닙니다 - 표로 되돌린 뒤 고치고 다시 바꾸세요.
            </p>
          ) : null}
        </div>
      ) : null}

      {convertTable !== null && convertIdx !== null ? (
        <ChartConvertDialog
          open
          onOpenChange={(open) => (open ? undefined : setConvertIdx(null))}
          table={convertTable}
          block={convertBlock}
          busy={busy}
          onConvert={(fence) => {
            setConvertIdx(null);
            void replaceBlock(convertBlock, fence, "표를 그래프로 바꿨습니다.");
          }}
        />
      ) : null}
    </article>
  );
}
