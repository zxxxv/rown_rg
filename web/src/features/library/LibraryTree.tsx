import {
  ChevronDown,
  ChevronRight,
  File as FileIcon,
  FileSpreadsheet,
  FileText,
  Folder,
  FolderOpen,
} from "lucide-react";
import { type DragEvent, useMemo, useState } from "react";
import type { LibraryNode, WritableTarget } from "@/api/types";
import { cn } from "@/lib/utils";

/** OS 파일을 끌고 있는 드래그인가 - 텍스트 선택·요소 드래그는 드롭 대상에서 제외. */
function hasFiles(e: DragEvent): boolean {
  return Array.from(e.dataTransfer.types).includes("Files");
}

function fileIcon(name: string): typeof FileIcon {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  if (ext === "pdf" || ext === "hwpx" || ext === "docx") return FileText;
  if (ext === "xlsx" || ext === "csv") return FileSpreadsheet;
  return FileIcon;
}

function highlight(name: string, q: string) {
  if (!q) return name;
  const idx = name.toLowerCase().indexOf(q);
  if (idx < 0) return name;
  return (
    <>
      {name.slice(0, idx)}
      <mark className="bg-bg-warning text-fg-warning">{name.slice(idx, idx + q.length)}</mark>
      {name.slice(idx + q.length)}
    </>
  );
}

export interface LibraryTreeProps {
  tree: LibraryNode[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  search: string;
  /** OS 파일을 폴더 행에 떨어뜨리면 그 폴더로 업로드 - 쓰기 가능한 폴더에만 열린다. */
  onDropFiles?: (target: WritableTarget, folderName: string, files: File[]) => void;
}

export function LibraryTree({ tree, selectedId, onSelect, search, onDropFiles }: LibraryTreeProps) {
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(["me", "company", "me-projects"]),
  );
  const q = search.trim().toLowerCase();

  const autoExpanded = useMemo(() => {
    if (!q) return null;
    const ids = new Set<string>();
    function walk(node: LibraryNode): boolean {
      if (node.type === "folder") {
        const childMatch = node.children.some((c) => walk(c));
        if (childMatch || node.name.toLowerCase().includes(q)) {
          ids.add(node.id);
          return true;
        }
        return false;
      }
      return node.name.toLowerCase().includes(q);
    }
    for (const n of tree) walk(n);
    return ids;
  }, [q, tree]);

  const isExpanded = (id: string) => {
    if (autoExpanded) return autoExpanded.has(id);
    return expanded.has(id);
  };

  const toggle = (id: string) => {
    if (autoExpanded) return; // 검색 중에는 자동 expand 고정
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const filtered = useMemo(() => {
    if (!q) return tree;
    function visit(node: LibraryNode): LibraryNode | null {
      if (node.type === "file") {
        return node.name.toLowerCase().includes(q) ? node : null;
      }
      const keptChildren = node.children.map(visit).filter((c): c is LibraryNode => c !== null);
      if (keptChildren.length === 0 && !node.name.toLowerCase().includes(q)) return null;
      return { ...node, children: keptChildren };
    }
    return tree.map(visit).filter((n): n is LibraryNode => n !== null);
  }, [q, tree]);

  return (
    <ul className="flex flex-col gap-0.5 p-2">
      {filtered.map((node) => (
        <TreeNodeView
          key={node.id}
          node={node}
          depth={0}
          selectedId={selectedId}
          onSelect={onSelect}
          isExpanded={isExpanded}
          onToggle={toggle}
          q={q}
          onDropFiles={onDropFiles}
        />
      ))}
      {filtered.length === 0 ? (
        <li className="px-3 py-4 text-center text-xs text-fg-tertiary">검색 결과 없음</li>
      ) : null}
    </ul>
  );
}

interface NodeViewProps {
  node: LibraryNode;
  depth: number;
  selectedId: string | null;
  onSelect: (id: string) => void;
  isExpanded: (id: string) => boolean;
  onToggle: (id: string) => void;
  q: string;
  onDropFiles?: LibraryTreeProps["onDropFiles"];
}

function TreeNodeView({
  node,
  depth,
  selectedId,
  onSelect,
  isExpanded,
  onToggle,
  q,
  onDropFiles,
}: NodeViewProps) {
  const selected = node.id === selectedId;
  const indent = { paddingLeft: `${8 + depth * 14}px` };
  // 파일을 끌어 올려놓은 폴더 행 강조 - 어디에 떨어질지 눈으로 확인하고 놓게.
  const [dragOver, setDragOver] = useState(false);

  if (node.type === "file") {
    const Icon = fileIcon(node.name);
    return (
      <li>
        <button
          type="button"
          onClick={() => onSelect(node.id)}
          style={indent}
          className={cn(
            "flex w-full items-center gap-2 rounded px-2 py-1 text-left text-xs transition-colors",
            selected
              ? "border-l-2 border-accent bg-bg-info text-fg"
              : "border-l-2 border-transparent text-fg-secondary hover:bg-bg-secondary/60",
          )}
        >
          <Icon className="h-3.5 w-3.5 shrink-0 text-fg-tertiary" aria-hidden />
          <span className="line-clamp-1 flex-1 font-mono">
            {q ? highlight(node.name, q) : node.name}
          </span>
        </button>
      </li>
    );
  }

  const expanded = isExpanded(node.id);
  // 쓰기 가능한 폴더만 드롭 대상 - 가상 컨테이너(프로젝트·프롬프트 등)는 반응하지 않는다.
  const droppable = Boolean(onDropFiles && node.writable);
  const writableTarget = node.writable ?? null;
  return (
    <li>
      {/* biome-ignore lint/a11y/noStaticElementInteractions: 드롭은 포인터 전용 보강 - 같은 업로드가 '파일 업로드' 버튼(키보드 가능)으로 열려 있다 */}
      <div
        style={indent}
        className={cn(
          "flex items-center gap-1 rounded px-1 py-1 text-sm",
          selected
            ? "border-l-2 border-accent bg-bg-info text-fg"
            : "border-l-2 border-transparent",
          !selected && !dragOver && "hover:bg-bg-secondary/60",
          dragOver && "border-l-2 border-accent bg-bg-info ring-1 ring-inset ring-accent",
        )}
        onDragOver={
          droppable
            ? (e) => {
                if (!hasFiles(e)) return;
                e.preventDefault(); // 기본 동작(브라우저가 파일을 여는 것)을 막아야 드롭이 성립한다
                e.stopPropagation();
                e.dataTransfer.dropEffect = "copy";
                setDragOver(true);
              }
            : undefined
        }
        onDragLeave={
          droppable
            ? (e) => {
                // 행 안의 버튼으로 이동해도 leave가 오므로 실제로 행을 벗어날 때만 끈다.
                if (e.currentTarget.contains(e.relatedTarget as Node)) return;
                setDragOver(false);
              }
            : undefined
        }
        onDrop={
          droppable
            ? (e) => {
                if (!hasFiles(e)) return;
                e.preventDefault();
                e.stopPropagation();
                setDragOver(false);
                const files = Array.from(e.dataTransfer.files);
                if (files.length > 0 && writableTarget && onDropFiles) {
                  onDropFiles(writableTarget, node.name, files);
                  // 접힌 폴더에 떨어뜨리면 결과가 안 보인다 - 펼쳐서 업로드된 파일을 보여준다.
                  if (!isExpanded(node.id)) onToggle(node.id);
                }
              }
            : undefined
        }
      >
        <button
          type="button"
          onClick={() => onToggle(node.id)}
          aria-label={expanded ? "접기" : "펼치기"}
          className="inline-flex h-4 w-4 items-center justify-center text-fg-tertiary hover:text-fg"
        >
          {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        </button>
        <button
          type="button"
          onClick={() => onSelect(node.id)}
          className="flex flex-1 items-center gap-2 text-left"
        >
          {expanded ? (
            <FolderOpen className="h-4 w-4 shrink-0 text-fg-info" aria-hidden />
          ) : (
            <Folder className="h-4 w-4 shrink-0 text-fg-info" aria-hidden />
          )}
          <span className="line-clamp-1 font-medium">
            {q ? highlight(node.name, q) : node.name}
          </span>
        </button>
      </div>
      {expanded ? (
        <ul className="flex flex-col gap-0.5">
          {node.children.map((child) => (
            <TreeNodeView
              key={child.id}
              node={child}
              depth={depth + 1}
              selectedId={selectedId}
              onSelect={onSelect}
              isExpanded={isExpanded}
              onToggle={onToggle}
              q={q}
              onDropFiles={onDropFiles}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}
