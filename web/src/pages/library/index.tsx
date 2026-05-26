import { FolderPlus, Search, Upload, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { useLibraryTree } from "@/api/library";
import type { LibraryNode } from "@/api/types";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { LibraryDetail } from "@/features/library/LibraryDetail";
import { LibraryTree } from "@/features/library/LibraryTree";
import { useAuth } from "@/hooks/useAuth";
import { useDebounce } from "@/hooks/useDebounce";
import { cn } from "@/lib/utils";

interface Lookup {
  node: LibraryNode | null;
  path: string[];
}

function findNode(tree: LibraryNode[], id: string, path: string[] = []): Lookup {
  for (const node of tree) {
    if (node.id === id) return { node, path };
    if (node.type === "folder") {
      const sub = findNode(node.children, id, [...path, node.name]);
      if (sub.node) return sub;
    }
  }
  return { node: null, path: [] };
}

export default function LibraryPage() {
  const { user, logout } = useAuth();
  const treeQuery = useLibraryTree();
  const tree = treeQuery.data?.tree ?? [];

  const [params, setParams] = useSearchParams();
  const initialQ = params.get("q") ?? "";
  const [searchInput, setSearchInput] = useState(initialQ);
  useEffect(() => {
    setSearchInput(initialQ);
  }, [initialQ]);
  const debouncedSearch = useDebounce(searchInput, 300);

  useEffect(() => {
    const trimmed = debouncedSearch.trim();
    if (trimmed === (params.get("q") ?? "")) return;
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (trimmed) next.set("q", trimmed);
        else next.delete("q");
        return next;
      },
      { replace: true },
    );
  }, [debouncedSearch, params, setParams]);

  const [selectedId, setSelectedId] = useState<string | null>(null);

  const lookup = useMemo(
    () => (selectedId ? findNode(tree, selectedId) : { node: null, path: [] }),
    [tree, selectedId],
  );

  return (
    <AppShell
      user={user ? { name: user.name, role: user.role } : null}
      onLogout={() => void logout()}
      tokenUsage={{ used: 1_240_000, limit: 5_000_000 }}
    >
      <div className="flex flex-col gap-4">
        <header className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h1 className="text-3xl font-semibold text-fg">자료 라이브러리</h1>
              <p className="text-sm text-fg-secondary">
                회사 공통 자료·클라이언트 자료·분석 표준 양식을 프로젝트별로 끌어다 사용합니다.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => toast("폴더 추가 (Phase 4에서 작동)")}
              >
                <FolderPlus className="mr-1 h-4 w-4" />
                폴더 추가
              </Button>
              <Button size="sm" onClick={() => toast("파일 업로드 (Phase 4에서 작동)")}>
                <Upload className="mr-1 h-4 w-4" />
                파일 업로드
              </Button>
            </div>
          </div>
          <div className="rounded border border-border-info bg-bg-info px-3 py-2 text-xs text-fg-info">
            Phase 4에서 react-arborist + 실 백엔드 연동으로 본격 작동 전환됩니다. 현재 업로드·폴더
            추가·권한 설정은 모킹 토스트로 표시됩니다.
          </div>
        </header>

        <div className="relative w-full max-w-md">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-tertiary"
            aria-hidden
          />
          <Input
            type="search"
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="폴더·파일 이름으로 검색"
            aria-label="라이브러리 검색"
            className={cn("pl-8", searchInput && "pr-8")}
          />
          {searchInput ? (
            <button
              type="button"
              aria-label="검색어 지우기"
              onClick={() => setSearchInput("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 text-fg-tertiary hover:text-fg"
            >
              <X className="h-4 w-4" />
            </button>
          ) : null}
        </div>

        {treeQuery.isLoading ? (
          <LoadingSkeleton variant="block" />
        ) : treeQuery.isError ? (
          <EmptyState
            title="라이브러리를 불러오지 못했습니다"
            description="잠시 후 다시 시도해 주세요."
            action={
              <Button variant="outline" onClick={() => void treeQuery.refetch()}>
                다시 시도
              </Button>
            }
          />
        ) : (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
            <ScrollArea className="h-[calc(100vh-260px)] rounded border border-border bg-bg">
              <LibraryTree
                tree={tree}
                selectedId={selectedId}
                onSelect={setSelectedId}
                search={debouncedSearch}
              />
            </ScrollArea>

            <main className="min-h-[400px] rounded border border-border bg-bg p-5">
              <LibraryDetail node={lookup.node} path={lookup.path} />
            </main>
          </div>
        )}
      </div>
    </AppShell>
  );
}
