import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  BarChart3,
  CheckCircle2,
  ExternalLink,
  Eye,
  Loader2,
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
import { progressKeys, useProgressSnapshot } from "@/api/progress";
import { useProject } from "@/api/projects";
import {
  useProjectSections,
  useRewriteBlock,
  useRewriteSection,
  useSaveSection,
  useSectionContent,
  useSectionEvidence,
} from "@/api/sections";
import type { ChapterNode, SectionCitation, SectionNode, SectionStatus } from "@/api/types";
import { useVerifyReport } from "@/api/verify";
import { StatusDot, type StatusKind } from "@/components/data-display/StatusDot";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { VerifyReportCard } from "@/features/export/VerifyReportCard";
import { ChartConvertDialog } from "@/features/preview/ChartConvertDialog";
import { chartFallbackTable } from "@/features/preview/chartSpec";
import { BlockEvidence, EvidencePanel } from "@/features/preview/EvidencePanel";
import { MarkdownContent } from "@/features/preview/MarkdownContent";
import { findTable, isTableCaption, type MarkdownTable } from "@/features/preview/tableToChart";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";

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
  const isGenerating =
    projectStatus === "researching" ||
    projectStatus === "indexing" ||
    projectStatus === "writing" ||
    projectStatus === "reviewing";
  const sectionsQuery = useProjectSections(projectId, isGenerating ? 5000 : undefined);

  // QA 게이트 대기 감지 - 열려 있으면 상단 승인 바 + 절별 정적검사 경고 표시
  const snapshotQuery = useProgressSnapshot(projectId, true, {
    refetchInterval: isGenerating ? 7000 : false,
  });
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
  const verifyQuery = useVerifyReport(projectId);
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
    if (!selectedRef || !verifyQuery.data) return [];
    return verifyQuery.data.filter((f) => {
      const ref = f.section_ref ?? "";
      if (!ref.startsWith(selectedRef)) return false;
      const next = ref[selectedRef.length];
      return next === undefined || !/[0-9]/.test(next); // '1.1'이 '1.12'에 오매칭되지 않게
    });
  }, [verifyQuery.data, selectedRef]);

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
  const jumpToRef = (ref: string) => {
    const head = ref.split(" ")[0]; // "1.1 표 1-2-1" 같은 표기에서 앞부분만
    const [ch, sec] = head.split(".").map((n) => Number.parseInt(n, 10));
    const target = tree[ch - 1]?.children[sec - 1];
    if (!target) {
      toast("해당 절을 찾지 못했습니다", { description: ref });
      return;
    }
    navigateTo(target.id);
  };

  const selectNode = (id: string, status: SectionStatus, isChapter: boolean) => {
    // 장은 항상 이동(하위 절 통독 뷰가 완성분만 골라 보여줌). 절은 본문 있는 것만.
    if (!isChapter && !VIEWABLE.includes(status)) {
      toast("아직 작성되지 않은 절입니다", {
        description: "작성이 완료되는 대로 순서대로 표시됩니다.",
      });
      return;
    }
    navigateTo(id);
  };

  return (
    <div className="flex flex-col gap-4">
      {qaPayload ? (
        <QaApproveBar projectId={projectId} payload={qaPayload} />
      ) : isGenerating ? (
        <div className="flex items-center gap-2 rounded border border-border-info bg-bg-info px-3 py-2 text-xs text-fg-info">
          <Loader2 className="h-4 w-4 shrink-0 animate-spin" aria-hidden />
          보고서 작성이 진행 중입니다 - 완성된 절부터 순서대로 표시되며, 그 자리에서 바로 편집할 수
          있습니다.
        </div>
      ) : null}

      {/* PM 검증 경고 - 고칠 수 있는 화면에 두되 접힌 한 줄로 시작(편집을 가리지 않게).
            절을 선택하면 그 절의 경고만 본문 위에 인라인 표시된다. */}
      <VerifyReportCard projectId={projectId} collapsible onJump={jumpToRef} />

      {sectionsQuery.isLoading ? (
        <LoadingSkeleton variant="block" />
      ) : tree.length === 0 && qaPayload ? (
        <PayloadDraftList payload={qaPayload} />
      ) : tree.length === 0 && isGenerating ? (
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
            evidenceOpen ? "" : "min-[1200px]:grid-cols-[240px_minmax(0,1fr)]",
          )}
        >
          {/* 트리는 내부 스크롤 없이 아래로 쭉 펼친다 - 목차 전체가 한눈에 보이고
                페이지 스크롤 하나로 탐색한다(사용자 요청, 2026-08-04).
                근거 드로어가 열려 있는 동안에는 접어 본문에 자리를 내준다. */}
          {evidenceOpen ? null : (
            <aside className="self-start rounded border border-border bg-bg">
              <SectionTree tree={tree} selectedId={selectedId} onSelect={selectNode} />
            </aside>
          )}

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
              />
            ) : selectedId ? (
              <SectionView
                key={selectedId}
                projectId={projectId}
                sectionId={selectedId}
                contentQuery={contentQuery}
                qaWarnings={qaWarnings}
                editable={!isGenerating}
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
function CitationList({ citations }: { citations: SectionCitation[] }) {
  if (citations.length === 0) return null;
  return (
    <footer className="mt-6 border-t border-border pt-3">
      <h3 className="mb-2 text-xs font-medium text-fg-secondary">
        이 절의 출처 {citations.length}건
      </h3>
      <ol className="flex flex-col gap-1">
        {citations.map((c, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: 위치가 곧 정체성 - source_ids 순서 고정 읽기 전용 목록(재정렬 없음)
          <li key={`cite-${i}`} className="flex items-start gap-2 text-xs">
            <span className="shrink-0 font-mono text-fg-tertiary">[{c.number ?? "–"}]</span>
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
}: {
  projectId: string;
  chapter: ChapterNode;
  chapterIndex: number;
  onOpenSection: (sectionId: string) => void;
}) {
  const completed = chapter.children.filter((s) => VIEWABLE.includes(s.status));
  return (
    <article className="flex flex-col">
      <header className="border-b border-border px-6 py-4">
        <p className="font-mono text-xs text-fg-tertiary">{chapterIndex + 1}장</p>
        <h2 className="text-lg font-semibold text-fg">{chapter.title}</h2>
        <p className="mt-1 text-xs text-fg-secondary">
          완성된 절 {completed.length}/{chapter.children.length}개 - 이어서 표시(읽기 전용). 편집은
          각 절을 여세요.
        </p>
      </header>
      {completed.length === 0 ? (
        <div className="p-6">
          <EmptyState
            icon={Eye}
            title="아직 완성된 절이 없습니다"
            description="이 장의 절이 작성 완료되면 여기 이어서 표시됩니다."
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
          <CitationList citations={query.data.citations} />
        </>
      ) : null}
    </section>
  );
}

/** 마크다운 본문 → 블록 목록. 빈 줄 경계로 나누되 각 블록은 원문의 정확한
 * 부분 문자열로 유지한다 - 블록 치환(직접 편집·AI 재작성)의 전제 조건.
 *
 * 두 가지를 붙여 둔다.
 * - 코드펜스(```chart 등)는 빈 줄이 들어 있어도 통째로 한 블록이다. 반으로 잘린 펜스는
 *   그래프로 그려지지도, 표로 되돌려지지도 않는다.
 * - **표 제목 줄은 표와 한 블록이다.** 제목은 표 위 문단으로 따로 쓰이지만 표의 일부다.
 *   떼어 놓으면 제목만 고르면 그래프 변환이 안 열리고, 표만 바꾸면 그림 위에 "표:"가
 *   남으며, 렌더러는 제목 없는 표로 보고 머리행으로 제목을 하나 더 지어낸다(제목 2개).
 *
 * 블록은 줄 범위를 잘라 만든다 - 사이 빈 줄까지 그대로 품어야 원문 부분 문자열로 남는다. */
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
    merged.push(range);
  }
  return merged.map(text);
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
  const evidenceQuery = useSectionEvidence(projectId, sectionId);
  const save = useSaveSection(projectId, sectionId);
  const rewrite = useRewriteSection(projectId, sectionId);
  const rewriteBlock = useRewriteBlock(projectId, sectionId);

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

  const blocks = useMemo(() => (data ? splitBlocks(data.content) : []), [data]);
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
  // 드로어가 뜨는 조건과 정확히 같은 식을 부모에 알린다 - 부모는 이때 절 트리를 접어
  // 자리를 내준다. 절을 옮기거나 화면을 떠날 때도 반드시 닫힘으로 되돌린다.
  const drawerOpen = editable && selectedBlocks.length > 0;
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
    if (!editable || editing || editingIdx !== null) return;
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
          ) : editable ? (
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

      {data.ungrounded.count > 0 ? (
        // 인용은 붙었지만 그 근거에 없는 수치 - 예타 실증에서 자료 없는 절이 예산·계수를
        // 지어내고 인용만 단 사례가 나왔다(2026-08-09). 사람이 가장 먼저 봐야 할 신호다.
        <div className="flex flex-col gap-1 border-b border-fg-danger/30 bg-bg-danger px-6 py-3">
          <p className="flex items-center gap-1.5 text-xs font-medium text-fg">
            <AlertTriangle className="h-3.5 w-3.5 text-fg-danger" aria-hidden />
            근거에서 확인되지 않는 수치 {data.ungrounded.count}개 - 인용이 붙어 있어도 원자료에 없는
            값일 수 있습니다
          </p>
          <p className="text-xs text-fg-secondary">
            {data.ungrounded.samples.join(", ")}
            {data.ungrounded.count > data.ungrounded.samples.length ? " …" : ""}
          </p>
        </div>
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

      {data.evidence.scarce ? (
        // 근거가 모자란 절은 분량 목표를 내려서 쓴다 - 그 사실을 본문에 적으면 납품물이
        // 더러워지므로(사용자 지적 2026-08-10) 본문 대신 여기서만 알린다.
        <div className="flex items-center gap-1.5 border-b border-fg-warning/30 bg-bg-warning px-6 py-2.5">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-fg-warning" aria-hidden />
          <p className="text-xs text-fg">
            자료 부족 절 - 확보된 근거
            {data.evidence.count !== null ? ` ${data.evidence.count}건` : ""}에 맞춰 분량을 줄여
            작성했습니다. 자료를 추가하고 이 절만 재작성하면 채워집니다.
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

              드로어가 열린 동안에는 그 폭만큼 오른쪽 여백을 준다. 겹쳐 띄우기만 하면
              본문 오른쪽이 잘려 정작 대조할 문장이 안 보인다(첫 구현에서 실제로 잘렸다).
              1440px 화면에서 본문과 460px 패널이 둘 다 온전할 자리는 없다 - 열었을 때만
              양보하고, 닫으면 즉시 전체 폭으로 돌아온다. */}
          <div
            className={cn(
              "min-w-0 px-6 py-4 transition-[padding] duration-200",
              editable && selectedBlocks.length > 0 && "xl:pr-[440px] 2xl:pr-[600px]",
            )}
          >
            {(rewrite.isPending || rewriteBlock.isPending) && (
              <div className="mb-3 flex items-center gap-2 rounded border border-border-info bg-bg-info px-3 py-2 text-xs text-fg-info">
                <Sparkles className="h-4 w-4 animate-pulse" />
                AI가 {rewriteBlock.isPending ? "선택한 블록을" : "이 섹션을"} 다시 작성하고
                있습니다…
              </div>
            )}
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
                    "relative cursor-pointer rounded border px-3 py-1.5 transition-colors",
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
                      <MarkdownContent
                        content={block}
                        citations={data.citations}
                        evidence={evidenceQuery.data?.items}
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
            {/* 출처 목록은 "이 자료"까지, 근거 추적은 "이 대목"까지 알려준다 - 창작 검증의 시작점 */}
            <EvidencePanel projectId={projectId} sectionId={sectionId} />
          </div>

          {/* 오른쪽 드로어: 근거 전용. 화면에 고정돼 본문 위로 뜨므로 본문 폭이 안 줄고,
              스크롤도 본문과 따로 논다 - 긴 근거를 읽는 동안 본문 위치를 잃지 않는다. */}
          {editable && selectedBlocks.length > 0 ? (
            <aside
              aria-label="선택한 블록의 근거"
              className="fixed inset-y-0 right-0 z-40 flex w-full max-w-[92vw] flex-col border-l border-border bg-bg-secondary shadow-xl sm:w-[460px] 2xl:w-[560px]"
            >
              <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-3">
                <p className="text-xs font-medium text-fg">블록 {selectedBlocks.length}개의 근거</p>
                <Button variant="ghost" size="sm" onClick={clearBlockSelection} disabled={busy}>
                  <X className="h-3.5 w-3.5" />
                  닫기
                </Button>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
                <BlockEvidence
                  projectId={projectId}
                  sectionId={sectionId}
                  blocks={selectedBlocks}
                />
              </div>
            </aside>
          ) : null}
        </div>
      )}
      {editable && !editing ? (
        // 선택 → 지시 → 실행이 가로 한 줄에 놓이도록 하단 띠로 뺐다. 평소에는 화면을
        // 차지하지 않고, 근거 패널이 세로 전체를 쓴다.
        <div className="sticky bottom-0 flex flex-wrap items-center gap-2 border-t border-border bg-bg px-6 py-3">
          <Label htmlFor="rewrite-instruction" className="text-xs text-fg-secondary">
            {selectedBlocks.length > 0 ? `블록 ${selectedBlocks.length}개` : "절 전체"} 재작성 지시
          </Label>
          <Textarea
            id="rewrite-instruction"
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            placeholder={
              selectedBlocks.length > 0
                ? "예: 더 간결하게, 수치를 앞세워서"
                : "예: 정책 시사점을 강조해서 다시 작성"
            }
            className="h-9 min-h-0 flex-1 resize-none py-2 text-xs"
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
          <p className="w-full text-[11px] leading-relaxed text-fg-tertiary">
            {chartSelected
              ? "그래프 블록은 AI 재작성 대상이 아닙니다 - 표로 되돌린 뒤 고치고 다시 바꾸세요."
              : selectedBlocks.length > 0
                ? "블록 재작성은 이 절이 이미 인용한 근거 안에서만 고칩니다 - 인용 번호·출처가 유지됩니다."
                : "절 전체 재작성은 프로젝트 자료에서 근거를 다시 검색해 처음부터 새로 씁니다."}
          </p>
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
