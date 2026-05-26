import {
  ChevronDown,
  ChevronRight,
  File as FileIcon,
  FileSpreadsheet,
  FileText,
  Folder,
  FolderOpen,
} from "lucide-react";
import { useMemo, useState } from "react";
import type { LibraryNode } from "@/api/types";
import { cn } from "@/lib/utils";

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
}

export function LibraryTree({ tree, selectedId, onSelect, search }: LibraryTreeProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
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
}

function TreeNodeView({
  node,
  depth,
  selectedId,
  onSelect,
  isExpanded,
  onToggle,
  q,
}: NodeViewProps) {
  const selected = node.id === selectedId;
  const indent = { paddingLeft: `${8 + depth * 14}px` };

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
  return (
    <li>
      <div
        style={indent}
        className={cn(
          "flex items-center gap-1 rounded px-1 py-1 text-sm",
          selected
            ? "border-l-2 border-accent bg-bg-info text-fg"
            : "border-l-2 border-transparent",
          !selected && "hover:bg-bg-secondary/60",
        )}
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
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}
