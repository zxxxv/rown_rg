import {
  ArrowUpRight,
  Download,
  FilePlus2,
  FileText,
  FolderOpen,
  Settings,
  Shield,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import {
  libraryFileDownloadUrl,
  resolveDownloadUrl,
  useDeleteNode,
  useSetNodeVisibility,
} from "@/api/library";
import { useProject } from "@/api/projects";
import type { LibraryNode, SourceKind, UserRoleType } from "@/api/types";
import { EmptyState } from "@/components/feedback/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { PromptBody, PromptCreateButton } from "@/features/library/PromptPanel";

interface LibraryDetailProps {
  node: LibraryNode | null;
  path: string[];
  /** 상단 파일 업로드 input을 여는 콜백(빈 폴더 안내 버튼용) */
  onRequestUpload?: () => void;
}

const KIND_LABEL: Record<SourceKind, string> = {
  gov: "정부·공공",
  academic: "학술",
  media: "언론",
  library: "내부",
  upload: "업로드",
  web_search: "웹 검색",
};

function formatSize(bytes: number): string {
  if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(1)} MB`;
  if (bytes >= 1_000) return `${(bytes / 1_000).toFixed(0)} KB`;
  return `${bytes} B`;
}

function countDescendants(node: LibraryNode): { folders: number; files: number; bytes: number } {
  if (node.type === "file") {
    return { folders: 0, files: 1, bytes: node.file_meta.size_bytes };
  }
  let folders = 0;
  let files = 0;
  let bytes = 0;
  for (const child of node.children) {
    const sub = countDescendants(child);
    if (child.type === "folder") folders += 1;
    folders += sub.folders;
    files += sub.files;
    bytes += sub.bytes;
  }
  return { folders, files, bytes };
}

export function LibraryDetail({ node, path, onRequestUpload }: LibraryDetailProps) {
  if (!node) {
    return (
      <EmptyState
        title="노드를 선택하세요"
        description="좌측 트리에서 폴더나 파일을 클릭하면 상세 정보가 표시됩니다."
      />
    );
  }

  return (
    <article className="flex flex-col gap-5">
      <header className="flex flex-col gap-2 border-b border-border pb-4">
        <Breadcrumb path={path} />
        <h2 className="text-xl font-semibold text-fg">{node.name}</h2>
      </header>

      {node.type === "folder" ? (
        <FolderBody node={node} onRequestUpload={onRequestUpload} />
      ) : node.prompt ? (
        <PromptBody prompt={node.prompt} />
      ) : (
        <FileBody node={node} />
      )}
    </article>
  );
}

function Breadcrumb({ path }: { path: string[] }) {
  if (path.length === 0) {
    return <p className="font-mono text-xs text-fg-tertiary">/</p>;
  }
  return <p className="font-mono text-xs text-fg-tertiary">/ {path.join(" / ")}</p>;
}

function FolderBody({
  node,
  onRequestUpload,
}: {
  node: Extract<LibraryNode, { type: "folder" }>;
  onRequestUpload?: () => void;
}) {
  const stats = countDescendants(node);
  const files = node.children.filter((c) => c.type === "file");
  const subfolders = node.children.filter((c) => c.type === "folder");
  // '내 에이전트'/'내 작성 규칙' 폴더는 새 프롬프트 생성 진입점.
  const isPromptContainer = node.id === "me-agents" || node.id === "me-rules";

  return (
    <div className="flex flex-col gap-4">
      <dl className="grid grid-cols-3 gap-3 rounded border border-border bg-bg-secondary p-3 font-mono text-sm">
        <Stat label="하위 폴더" value={`${stats.folders}개`} />
        <Stat label="하위 파일" value={`${stats.files}개`} />
        <Stat label="총 크기" value={formatSize(stats.bytes)} />
      </dl>

      {isPromptContainer ? (
        <div className="flex flex-wrap items-center gap-2">
          <PromptCreateButton folderId={node.id} />
          <span className="text-xs text-fg-tertiary">
            내 것을 만들면 보고서 생성 시 시스템 기본값보다 우선 적용됩니다.
          </span>
        </div>
      ) : node.virtual ? (
        <p className="rounded border border-dashed border-border bg-bg-secondary px-3 py-2 text-xs text-fg-tertiary">
          읽기 전용 폴더입니다 — 프로젝트·완성본 등 시스템이 구성한 뷰라 직접 편집할 수 없습니다.
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button
            onClick={() =>
              toast("이 폴더 전체를 현재 프로젝트에 추가 — 구현 예정", {
                description: `${stats.files}개 파일이 후보로 추가됩니다.`,
              })
            }
          >
            <FilePlus2 className="mr-1 h-4 w-4" />
            현재 프로젝트에 추가
          </Button>
          <DeleteNodeButton node={node} />
        </div>
      )}

      {subfolders.length > 0 ? (
        <section>
          <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-fg-tertiary">
            하위 폴더 ({subfolders.length})
          </h3>
          <ul className="grid grid-cols-2 gap-2 lg:grid-cols-3">
            {subfolders.map((sub) => (
              <li
                key={sub.id}
                className="flex items-center gap-2 rounded border border-border bg-bg p-3 text-sm"
              >
                <span className="font-medium text-fg">{sub.name}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {files.length > 0 ? (
        <section>
          <h3 className="mb-2 text-xs font-medium uppercase tracking-wide text-fg-tertiary">
            파일 ({files.length})
          </h3>
          <ul className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
            {files.map((f) => (f.type === "file" ? <FileCard key={f.id} node={f} /> : null))}
          </ul>
        </section>
      ) : (
        <EmptyState
          title="비어있는 폴더"
          description="파일을 업로드하거나 폴더를 추가하세요."
          action={
            onRequestUpload ? (
              <Button variant="outline" onClick={onRequestUpload}>
                파일 업로드
              </Button>
            ) : undefined
          }
        />
      )}
    </div>
  );
}

function FileCard({ node }: { node: Extract<LibraryNode, { type: "file" }> }) {
  return (
    <li className="flex flex-col gap-2 rounded border border-border bg-bg p-3">
      <div className="flex items-start justify-between gap-2">
        <span className="line-clamp-2 text-sm font-medium text-fg">{node.name}</span>
        <Badge variant="secondary" className="font-mono text-[10px]">
          {KIND_LABEL[node.file_meta.source_kind]}
        </Badge>
      </div>
      <div className="flex flex-wrap items-center gap-x-2 font-mono text-[11px] text-fg-tertiary">
        <span>{formatSize(node.file_meta.size_bytes)}</span>
        {node.file_meta.page_count !== undefined ? <span>{node.file_meta.page_count}p</span> : null}
        <span>{node.file_meta.registered_at.slice(0, 10)}</span>
      </div>
    </li>
  );
}

function FileBody({ node }: { node: Extract<LibraryNode, { type: "file" }> }) {
  const meta = node.file_meta;
  // 실파일은 표준 다운로드 URL, 가상파일은 download_url(완성본·웹출처 등)만. 없으면 버튼 숨김.
  const downloadHref = node.virtual
    ? node.download_url
      ? resolveDownloadUrl(node.download_url)
      : null
    : libraryFileDownloadUrl(node.id);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        {meta.project_id ? (
          <Badge variant="default" className="font-mono text-xs">
            <FolderOpen className="mr-1 h-3 w-3" aria-hidden />
            프로젝트 자료
          </Badge>
        ) : (
          <Badge variant="outline" className="font-mono text-xs">
            공용 자료
          </Badge>
        )}
        <Badge variant="secondary" className="font-mono">
          {KIND_LABEL[meta.source_kind]}
        </Badge>
        <span className="font-mono text-xs text-fg-tertiary">{formatSize(meta.size_bytes)}</span>
        {meta.page_count !== undefined ? (
          <span className="font-mono text-xs text-fg-tertiary">{meta.page_count}p</span>
        ) : null}
      </div>

      {meta.project_id ? <ProjectLinkRow projectId={meta.project_id} /> : null}

      <div
        role="img"
        aria-label={`${node.name} 미리보기`}
        className="flex aspect-[4/3] items-center justify-center rounded border border-dashed border-border bg-bg-secondary text-xs text-fg-tertiary"
      >
        미리보기 준비 중 — PDF·HWPX 1페이지 썸네일로 표시됩니다
      </div>

      <dl className="grid grid-cols-2 gap-3 rounded border border-border bg-bg p-3 text-sm">
        <Stat label="등록자" value={meta.registered_by} />
        <Stat label="등록일" value={meta.registered_at.slice(0, 10)} />
        <Stat label="크기" value={formatSize(meta.size_bytes)} />
        {meta.page_count !== undefined ? (
          <Stat label="페이지" value={`${meta.page_count}p`} />
        ) : null}
      </dl>

      <section>
        <h3 className="mb-2 flex items-center gap-2 text-sm font-medium uppercase tracking-wide text-fg-tertiary">
          <Shield className="h-3.5 w-3.5" aria-hidden />
          접근 권한
        </h3>
        <div className="flex flex-wrap gap-1.5">
          {meta.visible_to_roles.map((r) => (
            <Badge key={r} variant="secondary" className="font-mono text-xs">
              {r}
            </Badge>
          ))}
        </div>
      </section>

      <footer className="flex flex-wrap gap-2 border-t border-border pt-3">
        {!node.virtual ? (
          <Button
            onClick={() =>
              toast(`현재 프로젝트에 추가 — 구현 예정 (${node.name})`, {
                description: "자료 검토 단계에서 자동 후보로 등록됩니다.",
              })
            }
          >
            <FilePlus2 className="mr-1 h-4 w-4" />
            현재 프로젝트에 추가
          </Button>
        ) : null}
        {downloadHref ? (
          <Button variant="outline" onClick={() => downloadFile(downloadHref, node.name)}>
            <Download className="mr-1 h-4 w-4" />
            다운로드
          </Button>
        ) : null}
        {!node.virtual ? (
          <>
            <VisibilityDialogButton node={node} />
            <DeleteNodeButton node={node} />
          </>
        ) : null}
      </footer>

      <p className="flex items-center gap-1 text-xs text-fg-tertiary">
        <FileText className="h-3 w-3" aria-hidden />
        파일 ID: <span className="font-mono">{node.id}</span>
      </p>
    </div>
  );
}

function downloadFile(href: string, filename: string) {
  const a = document.createElement("a");
  a.href = href;
  if (/^https?:\/\//.test(href)) {
    // 외부 출처(웹 검색 등)는 새 탭으로 연다(cross-origin은 download 속성 무시됨).
    a.target = "_blank";
    a.rel = "noopener noreferrer";
  } else {
    a.download = filename;
  }
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function DeleteNodeButton({ node }: { node: LibraryNode }) {
  const deleteNode = useDeleteNode();
  const [open, setOpen] = useState(false);
  const isFolder = node.type === "folder";

  const onDelete = async () => {
    try {
      await deleteNode.mutateAsync(node.id);
      toast.success(`"${node.name}" 삭제 완료`);
      setOpen(false);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "삭제에 실패했습니다.";
      toast.error("삭제 실패", { description: msg });
    }
  };

  return (
    <>
      <Button
        variant="outline"
        className="text-fg-danger"
        onClick={() => setOpen(true)}
        disabled={deleteNode.isPending}
      >
        <Trash2 className="mr-1 h-4 w-4" />
        삭제
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{isFolder ? "폴더를 삭제할까요?" : "파일을 삭제할까요?"}</DialogTitle>
            <DialogDescription>
              "{node.name}"{isFolder ? "와 하위 폴더·파일 전체가" : "이(가)"} 영구 삭제됩니다. 이
              작업은 되돌릴 수 없습니다.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              취소
            </Button>
            <Button
              variant="destructive"
              onClick={() => void onDelete()}
              disabled={deleteNode.isPending}
            >
              {deleteNode.isPending ? "삭제 중…" : "삭제"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

const ROLE_OPTIONS: { value: UserRoleType; label: string }[] = [
  { value: "viewer", label: "뷰어" },
  { value: "worker", label: "작성자" },
  { value: "admin", label: "관리자" },
  { value: "super_admin", label: "최고관리자" },
];

function VisibilityDialogButton({ node }: { node: Extract<LibraryNode, { type: "file" }> }) {
  const setVisibility = useSetNodeVisibility();
  const [open, setOpen] = useState(false);
  const [roles, setRoles] = useState<string[]>(node.file_meta.visible_to_roles);

  const openDialog = () => {
    setRoles(node.file_meta.visible_to_roles);
    setOpen(true);
  };

  const toggle = (role: string) => {
    setRoles((prev) => (prev.includes(role) ? prev.filter((r) => r !== role) : [...prev, role]));
  };

  const onSave = async () => {
    try {
      await setVisibility.mutateAsync({ nodeId: node.id, visible_to_roles: roles });
      toast.success("접근 권한이 저장됐습니다.");
      setOpen(false);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "권한 저장에 실패했습니다.";
      toast.error("권한 저장 실패", { description: msg });
    }
  };

  return (
    <>
      <Button variant="ghost" onClick={openDialog}>
        <Settings className="mr-1 h-4 w-4" />
        권한 설정
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>접근 권한 설정</DialogTitle>
            <DialogDescription>
              이 파일을 열람할 수 있는 역할을 선택하세요. 관리자는 항상 볼 수 있으며, 변경은
              관리자만 할 수 있습니다.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2">
            {ROLE_OPTIONS.map((opt) => (
              <label
                key={opt.value}
                htmlFor={`vis-${opt.value}`}
                className="flex cursor-pointer items-center gap-2 rounded border border-border p-2.5"
              >
                <Checkbox
                  id={`vis-${opt.value}`}
                  checked={roles.includes(opt.value)}
                  onCheckedChange={() => toggle(opt.value)}
                />
                <Label htmlFor={`vis-${opt.value}`} className="cursor-pointer text-sm">
                  {opt.label}{" "}
                  <span className="font-mono text-xs text-fg-tertiary">{opt.value}</span>
                </Label>
              </label>
            ))}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              취소
            </Button>
            <Button onClick={() => void onSave()} disabled={setVisibility.isPending}>
              {setVisibility.isPending ? "저장 중…" : "저장"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-fg-tertiary">{label}</dt>
      <dd className="font-mono text-sm text-fg">{value}</dd>
    </div>
  );
}

function ProjectLinkRow({ projectId }: { projectId: string }) {
  const navigate = useNavigate();
  const projectQuery = useProject(projectId);
  const title = projectQuery.data?.title ?? projectId;
  return (
    <button
      type="button"
      onClick={() => navigate(`/projects/${projectId}/overview`)}
      className="flex items-center justify-between gap-2 rounded border border-border bg-bg-secondary px-3 py-2 text-left text-sm transition-colors hover:border-border-strong hover:bg-bg"
    >
      <span className="flex flex-col gap-0.5">
        <span className="text-xs text-fg-tertiary">소속 프로젝트</span>
        <span className="font-medium text-fg">{title}</span>
      </span>
      <ArrowUpRight className="h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
    </button>
  );
}
