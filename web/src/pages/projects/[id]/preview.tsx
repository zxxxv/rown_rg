import {
  AlertTriangle,
  ArrowLeft,
  ExternalLink,
  Eye,
  Pencil,
  Save,
  Sparkles,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { useProject } from "@/api/projects";
import {
  useProjectSections,
  useRewriteSection,
  useSaveSection,
  useSectionContent,
} from "@/api/sections";
import type { ChapterNode, SectionCitation, SectionNode, SectionStatus } from "@/api/types";
import { useVerifyReport } from "@/api/verify";
import { StatusDot, type StatusKind } from "@/components/data-display/StatusDot";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
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
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { VerifyReportCard } from "@/features/export/VerifyReportCard";
import { MarkdownContent } from "@/features/preview/MarkdownContent";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";

const STATUS_KIND: Record<SectionStatus, StatusKind> = {
  pending: "tertiary",
  writing: "info",
  completed: "success",
  failed: "danger",
};

export default function PreviewPage() {
  const { id: projectId = "" } = useParams<{ id: string }>();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { user, logout } = useAuth();

  const sectionsQuery = useProjectSections(projectId);
  const projectQuery = useProject(projectId);

  const tree = sectionsQuery.data?.tree ?? [];
  const selectedId = params.get("section") ?? findFirstCompleted(tree);
  // 선택된 id가 '장'이면 장 통합 뷰(하위 절 이어 보기), 아니면 절 본문.
  const selectedChapter = useMemo(
    () => tree.find((c) => c.id === selectedId) ?? null,
    [tree, selectedId],
  );
  // 장 선택 시엔 절 본문 쿼리를 끈다(장 id로는 본문이 없어 헛요청이 됨).
  const contentQuery = useSectionContent(projectId, selectedChapter ? null : selectedId);

  // 선택된 절의 PM 경고만 본문 위에 인라인 표시 — 고칠 대상 옆에 고칠 이유를 둔다.
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

  const selectNode = (id: string, status: SectionStatus, isChapter: boolean) => {
    // 장은 항상 이동(하위 절 통독 뷰가 완료분만 골라 보여줌). 절은 완료된 것만.
    if (!isChapter && status !== "completed") {
      toast(`아직 작성 중입니다 - ${id}`, {
        description: "완료된 섹션만 미리보기 할 수 있습니다.",
      });
      return;
    }
    navigateTo(id);
  };

  return (
    <AppShell
      user={user ? { name: user.name, role: user.role } : null}
      onLogout={() => void logout()}
      tokenUsage={{ used: 1_240_000, limit: 5_000_000 }}
    >
      <div className="flex flex-col gap-4">
        <header className="flex flex-col gap-2">
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
            <div>
              <h1 className="text-3xl font-semibold text-fg">섹션 미리보기·편집</h1>
              {projectQuery.data ? (
                <p className="mt-1 text-sm text-fg-secondary">{projectQuery.data.title}</p>
              ) : null}
            </div>
          </div>
        </header>

        {/* PM 검증 경고 — 고칠 수 있는 화면에 두되 접힌 한 줄로 시작(편집을 가리지 않게).
            절을 선택하면 그 절의 경고만 본문 위에 인라인 표시된다. */}
        <VerifyReportCard projectId={projectId} collapsible />

        {sectionsQuery.isLoading ? (
          <LoadingSkeleton variant="block" />
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
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">
            {/* 트리는 내부 스크롤 없이 아래로 쭉 펼친다 — 목차 전체가 한눈에 보이고
                페이지 스크롤 하나로 탐색한다(사용자 요청, 2026-08-04) */}
            <aside className="self-start rounded border border-border bg-bg">
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
                />
              ) : selectedId ? (
                <SectionView
                  key={selectedId}
                  projectId={projectId}
                  sectionId={selectedId}
                  contentQuery={contentQuery}
                />
              ) : (
                <EmptyState
                  title="섹션을 선택하세요"
                  description="좌측 트리에서 완료된 섹션을 클릭하면 본문이 표시됩니다."
                />
              )}
            </main>
          </div>
        )}
      </div>
    </AppShell>
  );
}

/** 절 하단 출처 목록 — 본문 [N]이 가리키는 자료를 번호순으로 나열한다. */
function CitationList({ citations }: { citations: SectionCitation[] }) {
  if (citations.length === 0) return null;
  return (
    <footer className="mt-6 border-t border-border pt-3">
      <h3 className="mb-2 text-xs font-medium text-fg-secondary">
        이 절의 출처 {citations.length}건
      </h3>
      <ol className="flex flex-col gap-1">
        {citations.map((c, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: 위치가 곧 정체성 — source_ids 순서 고정 읽기 전용 목록(재정렬 없음)
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

function findFirstCompleted(tree: ChapterNode[]): string | null {
  for (const ch of tree) {
    for (const sec of ch.children) {
      if (sec.status === "completed") return sec.id;
    }
    if (ch.status === "completed") return ch.id;
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
  /** 사람이 읽는 순번(1장, 1.1) — 실데이터 id는 UUID라 라벨로 못 쓴다 */
  label: string;
  title: SectionNode["title"];
  level: 1 | 2;
  status: SectionStatus;
  selected: boolean;
  onSelect: (id: string, status: SectionStatus, isChapter: boolean) => void;
}) {
  const dim = status !== "completed";
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

/** 장 통합 뷰 — 그 장의 완료된 절들을 이어서 표시(읽기 전용). 편집은 개별 절을 연다. */
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
  const completed = chapter.children.filter((s) => s.status === "completed");
  return (
    <article className="flex flex-col">
      <header className="border-b border-border px-6 py-4">
        <p className="font-mono text-xs text-fg-tertiary">{chapterIndex + 1}장</p>
        <h2 className="text-lg font-semibold text-fg">{chapter.title}</h2>
        <p className="mt-1 text-xs text-fg-secondary">
          완료된 절 {completed.length}/{chapter.children.length}개 - 이어서 표시(읽기 전용). 편집은
          각 절을 여세요.
        </p>
      </header>
      {completed.length === 0 ? (
        <div className="p-6">
          <EmptyState
            icon={Eye}
            title="아직 완료된 절이 없습니다"
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

/** 장 통합 뷰의 절 1개 — 본문을 읽기 전용으로 렌더하고 '편집'으로 그 절 편집 화면을 연다. */
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
        <h3 className="text-base font-semibold text-fg">
          <span className="mr-2 font-mono text-xs text-fg-tertiary">{refLabel}</span>
          {section.title}
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

function SectionView({
  projectId,
  sectionId,
  contentQuery,
}: {
  projectId: string;
  sectionId: string;
  contentQuery: ReturnType<typeof useSectionContent>;
}) {
  const data = contentQuery.data;
  const error = contentQuery.error;

  const save = useSaveSection(projectId, sectionId);
  const rewrite = useRewriteSection(projectId, sectionId);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [aiOpen, setAiOpen] = useState(false);
  const [instruction, setInstruction] = useState("");

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
              ? "이 섹션은 진행 패널에서 작성이 완료된 후 표시됩니다."
              : error instanceof ApiError
                ? error.message
                : "잠시 후 다시 시도해 주세요."
          }
        />
      </div>
    );
  }

  if (!data) return null;

  const startEdit = () => {
    setDraft(data.content);
    setEditing(true);
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

  const onRewrite = async () => {
    try {
      await rewrite.mutateAsync(instruction.trim());
      toast.success("AI가 이 섹션을 다시 작성했습니다.");
      setAiOpen(false);
      setInstruction("");
      setEditing(false);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "재작성에 실패했습니다.";
      toast.error("재작성 실패", { description: msg });
    }
  };

  const busy = save.isPending || rewrite.isPending;

  return (
    <article className="flex flex-col">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-6 py-4">
        <div className="flex items-center gap-2">
          {/* Lv 배지·UUID 접두는 개발 잔재라 제거(2026-08-05) — 순번은 트리·본문 헤딩에 이미 있다 */}
          <h2 className="text-lg font-semibold text-fg">{data.title}</h2>
          {data.qa_status === "passed" ? (
            <Badge variant="default" className="bg-bg-success text-fg-success">
              QA 통과
            </Badge>
          ) : data.qa_status === "failed" ? (
            <Badge variant="destructive">QA 실패</Badge>
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
          ) : (
            <>
              <Button variant="outline" size="sm" onClick={() => setAiOpen(true)} disabled={busy}>
                <Sparkles className="mr-1 h-4 w-4" />
                AI 재작성
              </Button>
              <Button size="sm" onClick={startEdit} disabled={busy}>
                <Pencil className="mr-1 h-4 w-4" />
                직접 편집
              </Button>
            </>
          )}
        </div>
      </header>

      <div className="px-6 py-4">
        {rewrite.isPending ? (
          <div className="mb-3 flex items-center gap-2 rounded border border-border-info bg-bg-info px-3 py-2 text-xs text-fg-info">
            <Sparkles className="h-4 w-4 animate-pulse" />
            AI가 근거를 검색해 이 섹션을 다시 작성하고 있습니다…
          </div>
        ) : null}
        {editing ? (
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="min-h-[420px] font-mono text-sm"
            aria-label="섹션 본문 편집"
          />
        ) : (
          <>
            <MarkdownContent content={data.content} citations={data.citations} />
            <CitationList citations={data.citations} />
          </>
        )}
      </div>

      <Dialog open={aiOpen} onOpenChange={setAiOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>AI 재작성</DialogTitle>
            <DialogDescription>
              프로젝트 자료에서 근거를 다시 검색해 이 섹션을 새로 작성합니다. 원하는 방향을
              적어주세요(비워두면 근거 기반으로 자연스럽게 다시 씁니다).
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ai-instruction">재작성 지시 (선택)</Label>
            <Textarea
              id="ai-instruction"
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              placeholder="예: 더 간결하게, 정책 시사점을 강조해서"
              className="min-h-[100px]"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAiOpen(false)} disabled={rewrite.isPending}>
              취소
            </Button>
            <Button onClick={() => void onRewrite()} disabled={rewrite.isPending}>
              <Sparkles className="mr-1 h-4 w-4" />
              {rewrite.isPending ? "작성 중…" : "재작성"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </article>
  );
}
