import { Check, ChevronDown, ChevronRight, FileText, Folder, Loader2, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { useLibraryTree } from "@/api/library";
import { useAttachLibrarySource, useProjectSources } from "@/api/sources";
import type { LibraryNode } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { formatSize } from "@/lib/format";

/** 자료 검토 화면의 인라인 라이브러리 패널 - 트리에서 체크로 골라 일괄 추가한다.

다이얼로그(구 LibraryPickerDialog)의 단건 클릭 추가를 대체(2026-08-08 사용자 결정:
업로드 절반 + 라이브러리 트리 절반, 체크 선택). 색인 가능한 실제 업로드 파일만
후보로 노출한다 - AI 수집 원문(content_url)·프롬프트·가상 노드는 원본 파일이 없어
색인할 수 없으므로 제외(구 다이얼로그와 동일 규칙). */

function isPickableFile(n: LibraryNode): boolean {
  return n.type !== "folder" && !n.virtual && !("prompt" in n && n.prompt) && !n.content_url;
}

function collectFiles(
  nodes: LibraryNode[],
  trail: string[],
): { id: string; name: string; path: string }[] {
  const out: { id: string; name: string; path: string }[] = [];
  for (const n of nodes) {
    if (n.type === "folder") {
      out.push(...collectFiles(n.children, [...trail, n.name]));
    } else if (isPickableFile(n)) {
      out.push({ id: n.id, name: n.name, path: trail.join(" / ") || "루트" });
    }
  }
  return out;
}

/** 이미 이 프로젝트에 불러온 파일 표식 - 체크박스는 잠기고 배지로 상태를 알린다. */
function AddedBadge() {
  return (
    <span className="inline-flex shrink-0 items-center gap-1 rounded-sm border border-fg-success/40 bg-bg-success px-1.5 py-0.5 text-[10px] font-medium text-fg-success">
      <Check className="h-3 w-3" aria-hidden />
      추가됨
    </span>
  );
}

export function LibraryTreePanel({ projectId }: { projectId: string }) {
  const treeQuery = useLibraryTree();
  const attach = useAttachLibrarySource(projectId);
  // 이미 이 프로젝트에 불러온 라이브러리 노드 - 트리에 '추가됨'으로 표시해
  // 같은 파일을 다시 고르지 않게 한다(재불러오기는 재색인이라 낭비).
  const sourcesQuery = useProjectSources(projectId);
  const attachedIds = useMemo(
    () =>
      new Set(
        (sourcesQuery.data?.items ?? [])
          .map((s) => s.library_file_id)
          .filter((v): v is string => Boolean(v)),
      ),
    [sourcesQuery.data],
  );
  const [q, setQ] = useState("");
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [adding, setAdding] = useState(false);

  const tree = treeQuery.data?.tree ?? [];
  const allFiles = useMemo(() => collectFiles(tree, []), [tree]);
  const searched = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return null;
    return allFiles.filter(
      (f) => f.name.toLowerCase().includes(needle) || f.path.toLowerCase().includes(needle),
    );
  }, [allFiles, q]);

  const toggleChecked = (id: string) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };
  const toggleCollapsed = (id: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // 순차 추가 - 백엔드가 건별 색인(파싱→청킹→임베딩)이라 병렬로 몰면 로컬 임베딩이 몰린다.
  const addSelected = async () => {
    if (adding || checked.size === 0) return;
    setAdding(true);
    const ids = [...checked];
    let ok = 0;
    const failed: string[] = [];
    for (const id of ids) {
      try {
        await attach.mutateAsync(id);
        ok += 1;
        setChecked((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
      } catch (err) {
        const name = allFiles.find((f) => f.id === id)?.name ?? id;
        const msg = err instanceof ApiError ? err.message : "불러오기 실패";
        failed.push(`${name}: ${msg}`);
      }
    }
    setAdding(false);
    if (ok > 0) toast.success(`라이브러리 자료 ${ok}건 불러오기·색인 완료`);
    if (failed.length > 0)
      toast.error(`${failed.length}건 실패`, { description: failed.join(" · ") });
  };

  return (
    <div className="flex flex-col gap-2 rounded border border-border bg-bg p-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium text-fg">라이브러리에서 선택</h3>
        <Button size="sm" onClick={addSelected} disabled={adding || checked.size === 0}>
          {adding ? (
            <>
              <Loader2 className="mr-1 h-4 w-4 animate-spin" aria-hidden />
              추가 중…
            </>
          ) : (
            `선택 ${checked.size}건 추가`
          )}
        </Button>
      </div>

      <div className="relative">
        <Search
          className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-tertiary"
          aria-hidden
        />
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="파일 이름·폴더 검색"
          className="h-8 pl-8"
        />
      </div>

      <div className="max-h-64 min-h-32 overflow-auto rounded border border-border bg-bg-secondary/40 p-1">
        {treeQuery.isLoading ? (
          <p className="py-8 text-center text-sm text-fg-tertiary">불러오는 중…</p>
        ) : allFiles.length === 0 ? (
          <p className="py-8 text-center text-sm text-fg-tertiary">
            불러올 파일이 없습니다 - 라이브러리에 파일을 먼저 업로드하세요.
          </p>
        ) : searched ? (
          searched.length === 0 ? (
            <p className="py-8 text-center text-sm text-fg-tertiary">검색 결과가 없습니다.</p>
          ) : (
            <ul className="flex flex-col">
              {searched.map((f) => (
                <li key={f.id}>
                  <label
                    htmlFor={`lib-pick-${f.id}`}
                    className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 hover:bg-bg-secondary"
                  >
                    <Checkbox
                      id={`lib-pick-${f.id}`}
                      checked={attachedIds.has(f.id) || checked.has(f.id)}
                      onCheckedChange={() => toggleChecked(f.id)}
                      disabled={adding || attachedIds.has(f.id)}
                    />
                    <FileText className="h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
                    <span className="flex-1 truncate text-sm text-fg">{f.name}</span>
                    {attachedIds.has(f.id) ? <AddedBadge /> : null}
                    <span className="truncate text-xs text-fg-tertiary">{f.path}</span>
                  </label>
                </li>
              ))}
            </ul>
          )
        ) : (
          <TreeLevel
            nodes={tree}
            depth={0}
            checked={checked}
            attachedIds={attachedIds}
            collapsed={collapsed}
            disabled={adding}
            onToggleChecked={toggleChecked}
            onToggleCollapsed={toggleCollapsed}
          />
        )}
      </div>
    </div>
  );
}

function TreeLevel({
  nodes,
  depth,
  checked,
  attachedIds,
  collapsed,
  disabled,
  onToggleChecked,
  onToggleCollapsed,
}: {
  nodes: LibraryNode[];
  depth: number;
  checked: Set<string>;
  /** 이미 프로젝트에 불러온 라이브러리 노드 id */
  attachedIds: Set<string>;
  collapsed: Set<string>;
  disabled: boolean;
  onToggleChecked: (id: string) => void;
  onToggleCollapsed: (id: string) => void;
}) {
  return (
    <ul className="flex flex-col">
      {nodes.map((n) => {
        if (n.type === "folder") {
          const isCollapsed = collapsed.has(n.id);
          // 체크 가능한 파일이 하나도 없는 폴더 가지는 통째로 숨긴다 - 빈 폴더 소음 제거
          if (collectFiles([n], []).length === 0) return null;
          return (
            <li key={n.id}>
              <button
                type="button"
                onClick={() => onToggleCollapsed(n.id)}
                className="flex w-full items-center gap-1.5 rounded px-2 py-1.5 text-left hover:bg-bg-secondary"
                style={{ paddingLeft: `${8 + depth * 16}px` }}
              >
                {isCollapsed ? (
                  <ChevronRight className="h-3.5 w-3.5 shrink-0 text-fg-tertiary" aria-hidden />
                ) : (
                  <ChevronDown className="h-3.5 w-3.5 shrink-0 text-fg-tertiary" aria-hidden />
                )}
                <Folder className="h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
                <span className="truncate text-sm text-fg">{n.name}</span>
              </button>
              {isCollapsed ? null : (
                <TreeLevel
                  nodes={n.children}
                  depth={depth + 1}
                  checked={checked}
                  attachedIds={attachedIds}
                  collapsed={collapsed}
                  disabled={disabled}
                  onToggleChecked={onToggleChecked}
                  onToggleCollapsed={onToggleCollapsed}
                />
              )}
            </li>
          );
        }
        if (!isPickableFile(n)) return null;
        return (
          <li key={n.id}>
            <label
              htmlFor={`lib-pick-${n.id}`}
              className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 hover:bg-bg-secondary"
              style={{ paddingLeft: `${8 + depth * 16}px` }}
            >
              <Checkbox
                id={`lib-pick-${n.id}`}
                checked={attachedIds.has(n.id) || checked.has(n.id)}
                onCheckedChange={() => onToggleChecked(n.id)}
                disabled={disabled || attachedIds.has(n.id)}
              />
              <FileText className="h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
              <span className="flex-1 truncate text-sm text-fg">{n.name}</span>
              {attachedIds.has(n.id) ? <AddedBadge /> : null}
              <span className="font-mono text-xs text-fg-tertiary">
                {formatSize(n.file_meta.size_bytes)}
              </span>
            </label>
          </li>
        );
      })}
    </ul>
  );
}
