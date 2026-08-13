import { FolderPlus, Search, Upload, X } from "lucide-react";
import { type DragEvent, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { useCreateFolder, useLibraryTree, useUploadFile } from "@/api/library";
import type { LibraryNode, WritableTarget } from "@/api/types";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { type UploadingFile, UploadProgressList } from "@/components/feedback/UploadProgressList";
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
  /** 조상 폴더 경로(자신 제외) - id를 실어 브레드크럼 클릭 이동에 쓴다. */
  path: { id: string; name: string }[];
}

function findNode(tree: LibraryNode[], id: string, path: Lookup["path"] = []): Lookup {
  for (const node of tree) {
    if (node.id === id) return { node, path };
    if (node.type === "folder") {
      const sub = findNode(node.children, id, [...path, { id: node.id, name: node.name }]);
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

  // 쓰기 대상 - 선택한 폴더의 writable(개인/회사·부모)을 따른다. 가상 컨테이너
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
  // 진행 중 업로드 - XHR 전송 바이트 실측(0~100). 버튼·드롭 두 경로가 같은 목록을 쓴다.
  const [uploads, setUploads] = useState<UploadingFile[]>([]);
  // 본문(상세) 영역에 파일을 끌어 올린 상태 - 선택한 폴더로 떨어진다는 표시.
  const [detailDragOver, setDetailDragOver] = useState(false);

  /** 파일들을 대상 폴더로 업로드 - 트리 드롭·본문 드롭·버튼 선택의 공통 경로. */
  const uploadTo = (target: WritableTarget, folderLabel: string, files: File[]) => {
    for (const file of files) {
      const rowId = `${folderLabel}:${file.name}-${file.size}-${file.lastModified}`;
      setUploads((u) =>
        u.some((f) => f.id === rowId) ? u : [...u, { id: rowId, name: file.name, progress: 0 }],
      );
      const onProgress = (percent: number) =>
        setUploads((u) => u.map((f) => (f.id === rowId ? { ...f, progress: percent } : f)));
      uploadFile.mutate(
        {
          file,
          parent_id: target.parent_id ?? null,
          is_personal: target.scope === "personal",
          onProgress,
        },
        {
          onSuccess: () => {
            setUploads((u) => u.filter((f) => f.id !== rowId));
            toast.success(`"${file.name}" 업로드 완료 (${folderLabel})`);
          },
          onError: (err: unknown) => {
            setUploads((u) => u.filter((f) => f.id !== rowId));
            const msg = err instanceof ApiError ? err.message : "업로드에 실패했습니다.";
            toast.error(`"${file.name}" 업로드 실패`, { description: msg });
          },
        },
      );
    }
  };

  const draggingFiles = (e: DragEvent) => Array.from(e.dataTransfer.types).includes("Files");

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

  const onPickFile = (files: FileList | null) => {
    if (!files?.length || !writable) return;
    uploadTo(writable, targetName, Array.from(files));
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  return (
    <AppShell
      user={user ? { name: user.name, role: user.role } : null}
      onLogout={() => void logout()}
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
                multiple
                className="hidden"
                aria-label="파일 선택"
                onChange={(e) => onPickFile(e.target.files)}
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
          // biome-ignore lint/a11y/noStaticElementInteractions: 빗나간 드롭의 브라우저 기본 동작만 막는 껍데기 - 조작 대상이 아니다
          <div
            className="grid grid-cols-1 gap-4 lg:grid-cols-[300px_minmax(0,1fr)]"
            // 드롭 대상을 빗나간 드롭이 브라우저 파일 열기로 새지 않게 페이지 수준에서 막는다.
            onDragOver={(e) => {
              if (draggingFiles(e)) e.preventDefault();
            }}
            onDrop={(e) => {
              if (draggingFiles(e)) e.preventDefault();
            }}
          >
            <div className="flex flex-col gap-2">
              <ScrollArea className="h-[calc(100vh-260px)] rounded border border-border bg-bg">
                <LibraryTree
                  tree={tree}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                  search={debouncedSearch}
                  onDropFiles={(target, name, files) => uploadTo(target, name, files)}
                />
              </ScrollArea>
              <UploadProgressList uploading={uploads} />
            </div>

            <main
              className={cn(
                "min-h-[400px] rounded border border-border bg-bg p-5",
                detailDragOver && canWrite && "border-accent ring-1 ring-inset ring-accent",
              )}
              // 본문 영역 드롭 = 지금 선택한 폴더로 업로드. 쓰기 불가 폴더면 반응하지 않는다.
              onDragOver={(e) => {
                if (!draggingFiles(e) || !canWrite) return;
                e.preventDefault();
                e.dataTransfer.dropEffect = "copy";
                setDetailDragOver(true);
              }}
              onDragLeave={(e) => {
                if (e.currentTarget.contains(e.relatedTarget as Node)) return;
                setDetailDragOver(false);
              }}
              onDrop={(e) => {
                if (!draggingFiles(e)) return;
                e.preventDefault();
                setDetailDragOver(false);
                if (!writable) return;
                const files = Array.from(e.dataTransfer.files);
                if (files.length > 0) uploadTo(writable, targetName, files);
              }}
            >
              <LibraryDetail
                node={lookup.node}
                path={lookup.path}
                onNavigate={setSelectedId}
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
              위치: {targetName || "-"}
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
