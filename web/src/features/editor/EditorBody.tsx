import { toast } from "sonner";
import type { EditorComponent } from "@/api/mock/fixtures/editor-sample";
import { MarkdownContent } from "@/features/preview/MarkdownContent";
import { cn } from "@/lib/utils";
import { type HoverActionKind, HoverActions } from "./HoverActions";
import { useRewriteDialog } from "./useRewriteDialog";

export interface EditorBodyProps {
  components: EditorComponent[];
  editMode: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

const ACTION_LABEL: Record<HoverActionKind, string> = {
  rewrite: "재작성",
  edit: "텍스트 편집",
  source: "출처 보기",
};

export function EditorBody({ components, editMode, selectedId, onSelect }: EditorBodyProps) {
  const { openRewrite } = useRewriteDialog();
  const handleAction = (compId: string, kind: HoverActionKind) => {
    if (kind === "rewrite") {
      openRewrite(compId);
      return;
    }
    toast(`${ACTION_LABEL[kind]} - ${compId}`, {
      description: "본격 작동은 구현 예정입니다.",
    });
  };

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col">
      {components.map((comp) => {
        const selected = comp.id === selectedId;
        return (
          <HoverActions
            key={comp.id}
            disabled={!editMode}
            onAction={(kind) => handleAction(comp.id, kind)}
          >
            <ComponentBlock
              component={comp}
              editMode={editMode}
              selected={selected}
              onClick={() => onSelect(comp.id)}
            />
          </HoverActions>
        );
      })}
    </div>
  );
}

function ComponentBlock({
  component,
  editMode,
  selected,
  onClick,
}: {
  component: EditorComponent;
  editMode: boolean;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <div
      className={cn(
        "group relative rounded-sm transition-colors",
        editMode ? "pl-5 hover:bg-bg-secondary/50" : "pl-0",
        selected && "bg-bg-info/40",
      )}
      data-component-id={component.id}
      data-component-type={component.type}
    >
      {editMode ? (
        <button
          type="button"
          onClick={onClick}
          aria-label={`컴포넌트 ${component.id} 선택`}
          className={cn(
            "absolute left-1 top-2 bottom-2 w-1 cursor-pointer rounded-full transition-colors",
            selected ? "bg-accent" : "bg-border group-hover:bg-fg-tertiary",
          )}
        />
      ) : null}
      <div className="px-4 py-1">
        <MarkdownContent content={component.markdown} />
      </div>
    </div>
  );
}
