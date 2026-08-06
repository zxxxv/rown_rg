import { FileText, Loader2, Plus, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { useLibraryTree } from "@/api/library";
import { useAttachLibrarySource } from "@/api/sources";
import type { LibraryNode } from "@/api/types";
import { EmptyState } from "@/components/feedback/EmptyState";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";

interface PickFile {
  id: string;
  name: string;
  sizeBytes: number;
  path: string;
}

// 업로드된 실제 파일만 후보로 — AI 수집 원문(content_url)·프롬프트·가상 노드는 원본 파일이
// 없어 색인할 수 없으므로 제외한다. 개인·회사 공유 폴더를 모두 훑는다(관리자는 사용자별 폴더 포함).
function collectFiles(nodes: LibraryNode[], trail: string[]): PickFile[] {
  const out: PickFile[] = [];
  for (const n of nodes) {
    if (n.type === "folder") {
      out.push(...collectFiles(n.children, [...trail, n.name]));
    } else if (!n.virtual && !n.prompt && !n.content_url) {
      out.push({
        id: n.id,
        name: n.name,
        sizeBytes: n.file_meta.size_bytes,
        path: trail.join(" / ") || "루트",
      });
    }
  }
  return out;
}

function formatSize(bytes: number): string {
  if (bytes <= 0) return "-";
  const kb = bytes / 1024;
  if (kb < 1024) return `${Math.round(kb)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}

export function LibraryPickerDialog({
  projectId,
  open,
  onOpenChange,
}: {
  projectId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const treeQuery = useLibraryTree();
  const attach = useAttachLibrarySource(projectId);
  const [q, setQ] = useState("");
  const [pendingId, setPendingId] = useState<string | null>(null);

  const files = useMemo(() => collectFiles(treeQuery.data?.tree ?? [], []), [treeQuery.data]);
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return files;
    return files.filter(
      (f) => f.name.toLowerCase().includes(needle) || f.path.toLowerCase().includes(needle),
    );
  }, [files, q]);

  const pick = (f: PickFile) => {
    if (attach.isPending) return;
    setPendingId(f.id);
    attach.mutate(f.id, {
      onSuccess: () => {
        toast.success(`"${f.name}" 불러오기·색인 완료`);
        setPendingId(null);
      },
      onError: (err: unknown) => {
        setPendingId(null);
        const msg = err instanceof ApiError ? err.message : "불러오기에 실패했습니다.";
        toast.error("불러오기 실패", { description: msg });
      },
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>라이브러리에서 불러오기</DialogTitle>
          <DialogDescription>
            개인·회사 공유 자료의 업로드 파일을 프로젝트 근거로 추가합니다. 선택하면 즉시 색인되며,
            PDF·HWPX·DOCX·MD·TXT만 색인할 수 있습니다.
          </DialogDescription>
        </DialogHeader>

        <div className="relative">
          <Search
            className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-tertiary"
            aria-hidden
          />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="파일 이름·폴더 검색"
            className="pl-8"
          />
        </div>

        {treeQuery.isLoading ? (
          <p className="py-8 text-center text-sm text-fg-tertiary">불러오는 중…</p>
        ) : filtered.length === 0 ? (
          <EmptyState
            title={files.length === 0 ? "불러올 파일이 없습니다" : "검색 결과가 없습니다"}
            description={
              files.length === 0
                ? "개인 자료나 회사 공유 자료에 파일을 먼저 업로드하세요."
                : "다른 검색어로 시도해 보세요."
            }
          />
        ) : (
          <ul className="flex max-h-[50vh] flex-col gap-1 overflow-auto">
            {filtered.map((f) => {
              const busy = pendingId === f.id;
              return (
                <li key={f.id}>
                  <button
                    type="button"
                    disabled={attach.isPending}
                    onClick={() => pick(f)}
                    className="flex w-full items-center gap-3 rounded border border-border bg-bg px-3 py-2 text-left transition-colors hover:bg-bg-secondary disabled:opacity-60"
                  >
                    <FileText className="h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
                    <span className="flex-1 truncate">
                      <span className="text-sm text-fg">{f.name}</span>
                      <span className="ml-2 text-xs text-fg-tertiary">{f.path}</span>
                    </span>
                    <span className="font-mono text-xs text-fg-tertiary">
                      {formatSize(f.sizeBytes)}
                    </span>
                    {busy ? (
                      <Loader2 className="h-4 w-4 shrink-0 animate-spin text-accent" aria-hidden />
                    ) : (
                      <Plus className="h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </DialogContent>
    </Dialog>
  );
}
