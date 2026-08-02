import { FolderPlus, Search, Upload, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { useCreateFolder, useLibraryTree, useUploadFile } from "@/api/library";
import type { LibraryNode } from "@/api/types";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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

  // 쓰기 대상 — 선택한 폴더의 writable(개인/회사·부모)을 따른다. 가상 컨테이너
  // (프로젝트·완성본 등)나 파일을 고르면 writable이 없어 업로드/폴더생성이 막힌다.
  const selectedFolder = lookup.node?.type === "folder" ? lookup.node : null;
  const writable = selectedFolder?.writable ?? null;
  const canWrite = Boolean(writable);
  const targetName = selectedFolder?.name ?? "";
  const targetParentId = writable?.parent_id ?? null;
  const targetIsPersonal = writable?.scope === "personal";

  const createFolder = useCreateFolder();
  const uploadFile = useUploadFile();
  const [folderDialogOpen, setFolderDialogOpen] = useState(false);
  const [folderName, setFolderName] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const onCreateFolder = async () => {
    const name = folderName.trim();
    if (!name || !writable) return;
    try {
      await createFolder.mutateAsync({
        name,
        parent_id: targetParentId,
        is_personal: targetIsPersonal,
      });
      toast.success(`폴더 "${name}"이(가) 추가됐습니다.`);
      setFolderDialogOpen(false);
      setFolderName("");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "폴더 생성에 실패했습니다.";
      toast.error("폴더 생성 실패", { description: msg });
    }
  };

  const onPickFile = async (files: FileList | null) => {
    const file = files?.[0];
    if (!file || !writable) return;
    try {
      await uploadFile.mutateAsync({
        file,
        parent_id: targetParentId,
        is_personal: targetIsPersonal,
      });
      toast.success(`"${file.name}" 업로드 완료 (${targetName})`);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "업로드에 실패했습니다.";
      toast.error("업로드 실패", { description: msg });
    } finally {
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

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
                내 프로젝트·개인 자료와 회사 공유 자료를 한 곳에서 관리합니다.{" "}
                {canWrite ? (
                  <span className="text-fg-tertiary">
                    업로드 위치: <span className="font-medium text-fg-secondary">{targetName}</span>
                  </span>
                ) : (
                  <span className="text-fg-tertiary">
                    업로드하려면 ‘내 자료’ 또는 ‘회사 공유’ 폴더를 선택하세요.
                  </span>
                )}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setFolderDialogOpen(true)}
                disabled={createFolder.isPending || !canWrite}
                title={canWrite ? undefined : "‘내 자료’ 또는 ‘회사 공유’ 폴더를 선택하세요"}
              >
                <FolderPlus className="mr-1 h-4 w-4" />
                폴더 추가
              </Button>
              <Button
                size="sm"
                onClick={() => fileInputRef.current?.click()}
                disabled={uploadFile.isPending || !canWrite}
                title={canWrite ? undefined : "‘내 자료’ 또는 ‘회사 공유’ 폴더를 선택하세요"}
              >
                <Upload className="mr-1 h-4 w-4" />
                {uploadFile.isPending ? "업로드 중…" : "파일 업로드"}
              </Button>
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                aria-label="파일 선택"
                onChange={(e) => void onPickFile(e.target.files)}
              />
            </div>
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
              <LibraryDetail
                node={lookup.node}
                path={lookup.path}
                onRequestUpload={() => fileInputRef.current?.click()}
              />
            </main>
          </div>
        )}
      </div>

      <Dialog open={folderDialogOpen} onOpenChange={setFolderDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>폴더 추가</DialogTitle>
            <DialogDescription>
              위치: {targetName || "—"}
              {targetIsPersonal ? " (개인)" : canWrite ? " (회사 공유)" : ""}
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="new-folder-name">폴더 이름</Label>
            <Input
              id="new-folder-name"
              value={folderName}
              onChange={(e) => setFolderName(e.target.value)}
              placeholder="예: 공용 분석 양식"
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void onCreateFolder();
                }
              }}
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setFolderDialogOpen(false)}>
              취소
            </Button>
            <Button
              onClick={() => void onCreateFolder()}
              disabled={createFolder.isPending || !folderName.trim()}
            >
              {createFolder.isPending ? "추가 중…" : "추가"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </AppShell>
  );
}
