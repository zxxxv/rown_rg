import { ArrowLeft, Eye, FileText, Save } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { EDITOR_SAMPLE } from "@/api/mock/fixtures/editor-sample";
import { useProject } from "@/api/projects";
import { useProjectSections } from "@/api/sections";
import type { ChapterNode } from "@/api/types";
import { AppShell } from "@/components/layout/AppShell";
import { KbdHint } from "@/components/primitives/KbdHint";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { ChapterTree } from "@/features/editor/ChapterTree";
import { EditorBody } from "@/features/editor/EditorBody";
import { InfoPanel } from "@/features/editor/InfoPanel";
import { RewriteDialog } from "@/features/editor/RewriteDialog";
import { useEditorSelection } from "@/features/editor/useEditorSelection";
import { RewriteDialogContext, useRewriteDialogState } from "@/features/editor/useRewriteDialog";
import { useAuth } from "@/hooks/useAuth";
import { useShortcut } from "@/hooks/useShortcut";
import { cn } from "@/lib/utils";

export default function EditorPage() {
  const { id: projectId = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { user, logout } = useAuth();

  const projectQuery = useProject(projectId);
  const sectionsQuery = useProjectSections(projectId);
  const tree = sectionsQuery.data?.tree ?? [];

  const [sectionId, setSectionId] = useState(EDITOR_SAMPLE.section_id);
  const [editMode, setEditMode] = useState(true);
  const { selectedId, select } = useEditorSelection();
  const rewriteDialog = useRewriteDialogState();

  useShortcut("e", () => setEditMode((v) => !v));

  const selectedComponent = useMemo(
    () => EDITOR_SAMPLE.components.find((c) => c.id === selectedId) ?? null,
    [selectedId],
  );

  return (
    <RewriteDialogContext.Provider value={rewriteDialog}>
      <AppShell
        user={user ? { name: user.name, role: user.role } : null}
        onLogout={() => void logout()}
        tokenUsage={{ used: 1_240_000, limit: 5_000_000 }}
      >
        <div className="flex h-[calc(100vh-120px)] flex-col gap-3">
          <Button
            variant="ghost"
            size="sm"
            className="w-fit text-fg-secondary"
            onClick={() => navigate(`/projects/${projectId}/overview`)}
          >
            <ArrowLeft className="mr-1 h-4 w-4" />
            프로젝트 개요
          </Button>

          <header className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-semibold text-fg">
                {projectQuery.data?.title ?? "보고서 편집"}
              </h1>
              <Badge variant="secondary" className="font-mono">
                {sectionId} · {EDITOR_SAMPLE.section_title}
              </Badge>
            </div>

            <ModeToggle editMode={editMode} onToggle={() => setEditMode((v) => !v)} />

            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => toast("HWPX 미리보기 — 구현 예정")}
              >
                <FileText className="mr-1 h-4 w-4" /> HWPX 미리보기
              </Button>
              <Button size="sm" onClick={() => toast("저장 — 본격 작동은 구현 예정")}>
                <Save className="mr-1 h-4 w-4" /> 저장
              </Button>
            </div>
          </header>

          <div className="rounded border border-border-info bg-bg-info px-3 py-2 text-xs text-fg-info">
            현재는 모킹 단계입니다. 텍스트 편집기 본격 작동은 추후 구현 예정 — 입력·재작성·저장은
            지금은 안내 토스트로만 동작합니다.
          </div>

          <ResizablePanelGroup
            direction="horizontal"
            className="flex-1 overflow-hidden rounded-lg border border-border bg-bg"
          >
            <ResizablePanel defaultSize={18} minSize={12} maxSize={30} className="bg-bg-secondary">
              <ChapterTree
                tree={tree}
                selectedSectionId={sectionId}
                onSelect={(id) => {
                  const status = findSectionStatus(tree, id);
                  if (status && status !== "completed") {
                    toast(`아직 작성 중입니다 — ${id}`, {
                      description: "완료된 섹션만 편집기에 표시됩니다.",
                    });
                    return;
                  }
                  setSectionId(EDITOR_SAMPLE.section_id);
                  toast(`섹션 이동 — 구현 예정 (${id})`, {
                    description: "현재는 데모 섹션(2.3) 한 개만 렌더됩니다.",
                  });
                }}
              />
            </ResizablePanel>

            <ResizableHandle />

            <ResizablePanel defaultSize={62} minSize={40}>
              <div className="h-full overflow-y-auto py-6">
                <EditorBody
                  components={EDITOR_SAMPLE.components}
                  editMode={editMode}
                  selectedId={selectedId}
                  onSelect={select}
                />
              </div>
            </ResizablePanel>

            <ResizableHandle />

            <ResizablePanel defaultSize={20} minSize={16} maxSize={30} className="bg-bg-secondary">
              <InfoPanel component={selectedComponent} />
            </ResizablePanel>
          </ResizablePanelGroup>
        </div>
        <RewriteDialog />
      </AppShell>
    </RewriteDialogContext.Provider>
  );
}

function ModeToggle({ editMode, onToggle }: { editMode: boolean; onToggle: () => void }) {
  return (
    <div
      role="tablist"
      aria-label="에디터 모드"
      className="inline-flex items-center gap-1 rounded-lg border border-border bg-bg-secondary p-0.5"
    >
      <button
        type="button"
        role="tab"
        aria-selected={!editMode}
        onClick={() => {
          if (editMode) onToggle();
        }}
        className={cn(
          "inline-flex items-center gap-1 rounded-md px-3 py-1 text-xs transition-colors",
          !editMode ? "bg-bg text-fg shadow-sm" : "text-fg-secondary hover:text-fg",
        )}
      >
        <Eye className="h-3.5 w-3.5" /> 검토 모드
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={editMode}
        onClick={() => {
          if (!editMode) onToggle();
        }}
        className={cn(
          "inline-flex items-center gap-2 rounded-md px-3 py-1 text-xs transition-colors",
          editMode ? "bg-bg text-fg shadow-sm" : "text-fg-secondary hover:text-fg",
        )}
      >
        편집 모드
        <KbdHint keys={["E"]} />
      </button>
    </div>
  );
}

function findSectionStatus(tree: ChapterNode[], id: string) {
  for (const ch of tree) {
    if (ch.id === id) return ch.status;
    for (const sec of ch.children) {
      if (sec.id === id) return sec.status;
    }
  }
  return null;
}
