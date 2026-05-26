import { Eye, MoreHorizontal, RotateCw } from "lucide-react";
import { toast } from "sonner";
import type { ChapterNode, SectionStatus } from "@/api/types";
import { StatusDot, type StatusKind } from "@/components/data-display/StatusDot";
import { cn } from "@/lib/utils";

const STATUS_KIND: Record<SectionStatus, StatusKind> = {
  pending: "tertiary",
  writing: "info",
  completed: "success",
  failed: "danger",
};

export interface ChapterTreeProps {
  tree: ChapterNode[];
  selectedSectionId: string;
  onSelect: (sectionId: string) => void;
}

export function ChapterTree({ tree, selectedSectionId, onSelect }: ChapterTreeProps) {
  return (
    <nav aria-label="챕터·섹션 트리" className="flex flex-col">
      <header className="px-3 py-2 text-[10px] font-medium uppercase tracking-wide text-fg-tertiary">
        탐색
      </header>
      <ul className="flex flex-col gap-0.5 px-1 pb-2">
        {tree.map((chapter) => (
          <li key={chapter.id} className="flex flex-col gap-0.5">
            <Item
              id={chapter.id}
              title={chapter.title}
              status={chapter.status}
              level={1}
              selected={selectedSectionId === chapter.id}
              onSelect={onSelect}
            />
            {chapter.children.map((section) => (
              <Item
                key={section.id}
                id={section.id}
                title={section.title}
                status={section.status}
                level={2}
                selected={selectedSectionId === section.id}
                onSelect={onSelect}
              />
            ))}
          </li>
        ))}
      </ul>
    </nav>
  );
}

function Item({
  id,
  title,
  status,
  level,
  selected,
  onSelect,
}: {
  id: string;
  title: string;
  status: SectionStatus;
  level: 1 | 2;
  selected: boolean;
  onSelect: (id: string) => void;
}) {
  const dim = status !== "completed";
  return (
    <div
      className={cn(
        "group relative flex items-center gap-2 rounded px-2 py-1 text-sm transition-colors",
        level === 1 ? "font-medium" : "pl-5 text-xs",
        selected ? "border-l-2 border-accent bg-bg-info text-fg" : "border-l-2 border-transparent",
        dim ? "text-fg-tertiary" : "text-fg",
        !selected && "hover:bg-bg-tertiary/60",
      )}
    >
      <button
        type="button"
        onClick={() => onSelect(id)}
        className="flex flex-1 items-center gap-2 text-left"
      >
        <span className="font-mono text-[10px] text-fg-tertiary">{id}</span>
        <span className="line-clamp-1 flex-1">{title}</span>
        <StatusDot kind={STATUS_KIND[status]} />
      </button>
      <div className="hidden gap-0.5 group-hover:flex">
        <IconAction
          icon={RotateCw}
          label={`${id} 재작성`}
          onClick={() => toast("재작성 (Phase 5에서 작동)")}
        />
        <IconAction
          icon={Eye}
          label={`${id} 미리보기`}
          onClick={() => toast("미리보기 (Phase 5에서 작동)")}
        />
        <IconAction
          icon={MoreHorizontal}
          label={`${id} 더보기`}
          onClick={() => toast("더보기 (Phase 5에서 작동)")}
        />
      </div>
    </div>
  );
}

function IconAction({
  icon: Icon,
  label,
  onClick,
}: {
  icon: typeof Eye;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      className="inline-flex h-5 w-5 items-center justify-center rounded-sm text-fg-tertiary hover:bg-bg-tertiary hover:text-fg"
    >
      <Icon className="h-3 w-3" aria-hidden />
    </button>
  );
}
