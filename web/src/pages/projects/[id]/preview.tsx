import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Eye, Pencil, Save, Sparkles, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { useProject } from "@/api/projects";
import {
  getSourceRef,
  sourceRefKeys,
  useProjectSections,
  useRewriteSection,
  useSaveSection,
  useSectionContent,
} from "@/api/sections";
import type { ChapterNode, SectionNode, SectionStatus } from "@/api/types";
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
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
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
  const qc = useQueryClient();

  const sectionsQuery = useProjectSections(projectId);
  const projectQuery = useProject(projectId);

  const tree = sectionsQuery.data?.tree ?? [];
  const selectedId = params.get("section") ?? findFirstCompleted(tree);
  const contentQuery = useSectionContent(projectId, selectedId);

  // Prefetch source refs for hover cards
  useEffect(() => {
    const ids = contentQuery.data?.source_ids;
    if (!ids || ids.length === 0) return;
    for (const srcId of ids) {
      void qc.prefetchQuery({
        queryKey: sourceRefKeys.detail(srcId),
        queryFn: () => getSourceRef(srcId),
        staleTime: Number.POSITIVE_INFINITY,
      });
    }
  }, [contentQuery.data?.source_ids, qc]);

  const selectSection = (sectionId: string, status: SectionStatus) => {
    if (status !== "completed") {
      toast(`아직 작성 중입니다 — ${sectionId}`, {
        description: "완료된 섹션만 미리보기 할 수 있습니다.",
      });
      return;
    }
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("section", sectionId);
        return next;
      },
      { replace: true },
    );
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
            <ScrollArea className="h-[calc(100vh-220px)] rounded border border-border bg-bg">
              <SectionTree tree={tree} selectedId={selectedId} onSelect={selectSection} />
            </ScrollArea>

            <main className="rounded border border-border bg-bg">
              {selectedId ? (
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
  onSelect: (id: string, status: SectionStatus) => void;
}

function SectionTree({ tree, selectedId, onSelect }: SectionTreeProps) {
  return (
    <nav aria-label="섹션 트리" className="flex flex-col p-2">
      {tree.map((chapter) => (
        <div key={chapter.id} className="mb-2">
          <TreeItem
            id={chapter.id}
            title={chapter.title}
            level={1}
            status={chapter.status}
            selected={selectedId === chapter.id}
            onSelect={onSelect}
          />
          <ul className="ml-2 flex flex-col">
            {chapter.children.map((section) => (
              <li key={section.id}>
                <TreeItem
                  id={section.id}
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
  title,
  level,
  status,
  selected,
  onSelect,
}: {
  id: string;
  title: SectionNode["title"];
  level: 1 | 2;
  status: SectionStatus;
  selected: boolean;
  onSelect: (id: string, status: SectionStatus) => void;
}) {
  const dim = status !== "completed";
  return (
    <button
      type="button"
      onClick={() => onSelect(id, status)}
      className={cn(
        "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm transition-colors",
        level === 1 ? "font-medium" : "pl-6 text-xs",
        selected ? "border border-accent bg-bg-info text-fg" : "border border-transparent",
        dim ? "text-fg-tertiary" : "text-fg",
        !selected && "hover:bg-bg-secondary",
      )}
    >
      <span className="font-mono text-[11px] text-fg-tertiary">{id}</span>
      <span className="line-clamp-1 flex-1">{title}</span>
      <StatusDot kind={STATUS_KIND[status]} />
    </button>
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
          <Badge variant="secondary" className="font-mono">
            Lv {data.level}
          </Badge>
          <h2 className="text-lg font-semibold text-fg">
            <span className="font-mono text-fg-tertiary">{sectionId.slice(0, 8)}</span> {data.title}
          </h2>
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
          <MarkdownContent content={data.content} />
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
