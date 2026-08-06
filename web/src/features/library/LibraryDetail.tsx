import {
  ArrowUpRight,
  ChevronDown,
  ChevronUp,
  Download,
  FileText,
  Folder,
  FolderOpen,
  Settings,
  Shield,
  Trash2,
} from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import {
  libraryFileDownloadUrl,
  resolveDownloadUrl,
  useDeleteNode,
  useSetNodeVisibility,
  useSourceContent,
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { PromptBody } from "@/features/library/PromptPanel";
import { MarkdownContent } from "@/features/preview/MarkdownContent";

interface LibraryDetailProps {
  node: LibraryNode | null;
  /** 조상 폴더 경로(자신 제외) — 브레드크럼 클릭 이동용 id 포함 */
  path: { id: string; name: string }[];
  /** 우측 패널에서의 이동(폴더 진입·파일 열기·브레드크럼) — 좌측 트리 선택과 동일 상태 */
  onNavigate?: (id: string) => void;
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

function countDescendants(node: LibraryNode): {
  folders: number;
  files: number;
  bytes: number;
  /** 하위에서 가장 최근 등록일(ISO) — 폴더 행의 '최근 등록' 표시용 */
  latest: string | null;
} {
  if (node.type === "file") {
    return {
      folders: 0,
      files: 1,
      bytes: node.file_meta.size_bytes,
      latest: node.file_meta.registered_at,
    };
  }
  let folders = 0;
  let files = 0;
  let bytes = 0;
  let latest: string | null = null;
  for (const child of node.children) {
    const sub = countDescendants(child);
    if (child.type === "folder") folders += 1;
    folders += sub.folders;
    files += sub.files;
    bytes += sub.bytes;
    if (sub.latest && (!latest || sub.latest > latest)) latest = sub.latest;
  }
  return { folders, files, bytes, latest };
}

export function LibraryDetail({ node, path, onNavigate, onRequestUpload }: LibraryDetailProps) {
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
        <Breadcrumb path={path} onNavigate={onNavigate} />
        <h2 className="text-xl font-semibold text-fg">{node.name}</h2>
      </header>

      {node.type === "folder" ? (
        <FolderBody node={node} onNavigate={onNavigate} onRequestUpload={onRequestUpload} />
      ) : node.prompt ? (
        <PromptBody prompt={node.prompt} />
      ) : (
        <FileBody node={node} />
      )}
    </article>
  );
}

function Breadcrumb({
  path,
  onNavigate,
}: {
  path: { id: string; name: string }[];
  onNavigate?: (id: string) => void;
}) {
  if (path.length === 0) {
    return <p className="font-mono text-xs text-fg-tertiary">/</p>;
  }
  return (
    <p className="flex flex-wrap items-center gap-1 font-mono text-xs text-fg-tertiary">
      /
      {path.map((seg) => (
        <span key={seg.id} className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => onNavigate?.(seg.id)}
            className="rounded px-0.5 transition-colors hover:bg-bg-secondary hover:text-fg"
          >
            {seg.name}
          </button>
          <span aria-hidden>/</span>
        </span>
      ))}
    </p>
  );
}

// 폴더 목록 정렬 축 — 폴더가 항상 파일보다 먼저 오고, 그 안에서 정렬한다.
type SortKey = "name" | "kind" | "size" | "registrant" | "date";

interface FolderRow {
  node: LibraryNode;
  isFolder: boolean;
  name: string;
  kind: string;
  sizeBytes: number;
  pages: number | null;
  registrant: string | null;
  /** ISO 등록일 — 폴더는 하위 최근 등록일 */
  date: string | null;
  isProject: boolean;
}

function FolderBody({
  node,
  onNavigate,
  onRequestUpload,
}: {
  node: Extract<LibraryNode, { type: "folder" }>;
  onNavigate?: (id: string) => void;
  onRequestUpload?: () => void;
}) {
  const navigate = useNavigate();
  const stats = countDescendants(node);
  // '내 에이전트'/'내 작성 규칙' 폴더 — 라이브러리에선 읽기 전용, 편집은 프롬프트 관리 페이지.
  const isPromptContainer = node.id === "me-agents" || node.id === "me-rules";

  const [sortKey, setSortKey] = useState<SortKey>("name");
  const [sortAsc, setSortAsc] = useState(true);
  const toggleSort = (key: SortKey) => {
    if (key === sortKey) setSortAsc((v) => !v);
    else {
      setSortKey(key);
      setSortAsc(true);
    }
  };

  const rows = useMemo<FolderRow[]>(() => {
    const mapped = node.children.map<FolderRow>((child) => {
      if (child.type === "folder") {
        const sub = countDescendants(child);
        return {
          node: child,
          isFolder: true,
          name: child.name,
          kind: "폴더",
          sizeBytes: sub.bytes,
          pages: null,
          registrant: null,
          date: sub.latest,
          isProject: false,
        };
      }
      return {
        node: child,
        isFolder: false,
        name: child.name,
        kind: KIND_LABEL[child.file_meta.source_kind],
        sizeBytes: child.file_meta.size_bytes,
        pages: child.file_meta.page_count ?? null,
        registrant: child.file_meta.registered_by,
        date: child.file_meta.registered_at,
        isProject: Boolean(child.file_meta.project_id),
      };
    });
    const dir = sortAsc ? 1 : -1;
    const cmp = (a: FolderRow, b: FolderRow): number => {
      if (a.isFolder !== b.isFolder) return a.isFolder ? -1 : 1;
      switch (sortKey) {
        case "size":
          return (a.sizeBytes - b.sizeBytes) * dir;
        case "date":
          return (a.date ?? "").localeCompare(b.date ?? "") * dir;
        case "kind":
          return a.kind.localeCompare(b.kind, "ko") * dir;
        case "registrant":
          return (a.registrant ?? "").localeCompare(b.registrant ?? "", "ko") * dir;
        default:
          return a.name.localeCompare(b.name, "ko") * dir;
      }
    };
    return mapped.sort(cmp);
  }, [node.children, sortKey, sortAsc]);

  return (
    <div className="flex flex-col gap-4">
      <p className="font-mono text-xs text-fg-tertiary">
        하위 폴더 {stats.folders}개 · 파일 {stats.files}개 · 총 {formatSize(stats.bytes)}
      </p>

      {isPromptContainer ? (
        <div className="flex flex-wrap items-center gap-2 rounded border border-dashed border-border bg-bg-secondary px-3 py-2">
          <span className="text-xs text-fg-tertiary">
            라이브러리에선 읽기 전용입니다 - 편집·추가는 프롬프트 관리 페이지에서 하세요.
          </span>
          <Button variant="outline" size="sm" onClick={() => navigate("/prompts")}>
            프롬프트 관리로 가기
          </Button>
        </div>
      ) : node.virtual ? (
        <p className="rounded border border-dashed border-border bg-bg-secondary px-3 py-2 text-xs text-fg-tertiary">
          읽기 전용 폴더입니다 - 프로젝트·완성본 등 시스템이 구성한 뷰라 직접 편집할 수 없습니다.
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          <DeleteNodeButton node={node} />
        </div>
      )}

      {rows.length > 0 ? (
        <div className="overflow-x-auto rounded border border-border">
          <Table>
            <TableHeader>
              <TableRow>
                <SortHead
                  label="이름"
                  me="name"
                  sortKey={sortKey}
                  asc={sortAsc}
                  onSort={toggleSort}
                />
                <SortHead
                  label="종류"
                  me="kind"
                  sortKey={sortKey}
                  asc={sortAsc}
                  onSort={toggleSort}
                />
                <SortHead
                  label="크기"
                  me="size"
                  sortKey={sortKey}
                  asc={sortAsc}
                  onSort={toggleSort}
                />
                <TableHead className="text-right font-mono text-xs">페이지</TableHead>
                <SortHead
                  label="등록자"
                  me="registrant"
                  sortKey={sortKey}
                  asc={sortAsc}
                  onSort={toggleSort}
                />
                <SortHead
                  label="등록일"
                  me="date"
                  sortKey={sortKey}
                  asc={sortAsc}
                  onSort={toggleSort}
                />
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow
                  key={row.node.id}
                  onClick={() => onNavigate?.(row.node.id)}
                  className="cursor-pointer"
                >
                  <TableCell className="max-w-[320px]">
                    <span className="flex items-center gap-2">
                      {row.isFolder ? (
                        <Folder className="h-4 w-4 shrink-0 text-fg-info" aria-hidden />
                      ) : (
                        <FileText className="h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
                      )}
                      <span className="truncate text-sm font-medium text-fg">{row.name}</span>
                      {row.isProject ? (
                        <Badge variant="outline" className="shrink-0 font-mono text-[10px]">
                          프로젝트
                        </Badge>
                      ) : null}
                    </span>
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-fg-secondary">
                    {row.kind}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-right font-mono text-xs text-fg-secondary">
                    {formatSize(row.sizeBytes)}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-right font-mono text-xs text-fg-tertiary">
                    {row.pages !== null ? `${row.pages}p` : "-"}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-fg-secondary">
                    {row.registrant ?? "-"}
                  </TableCell>
                  <TableCell className="whitespace-nowrap font-mono text-xs text-fg-tertiary">
                    {row.date ? row.date.slice(0, 10) : "-"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
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

function SortHead({
  label,
  me,
  sortKey,
  asc,
  onSort,
}: {
  label: string;
  me: SortKey;
  sortKey: SortKey;
  asc: boolean;
  onSort: (key: SortKey) => void;
}) {
  const active = sortKey === me;
  const alignRight = me === "size";
  return (
    <TableHead className={alignRight ? "text-right" : undefined}>
      <button
        type="button"
        onClick={() => onSort(me)}
        className={`inline-flex items-center gap-1 font-mono text-xs transition-colors hover:text-fg ${
          active ? "text-fg" : "text-fg-tertiary"
        }`}
      >
        {label}
        {active ? (
          asc ? (
            <ChevronUp className="h-3 w-3" aria-hidden />
          ) : (
            <ChevronDown className="h-3 w-3" aria-hidden />
          )
        ) : null}
      </button>
    </TableHead>
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
  // AI 수집 자료는 수집 원문을 라이브러리 안에서 바로 렌더(content_url 있을 때).
  const collectedUrl = node.content_url ?? null;
  // 외부 원문 링크(웹 검색)는 다운로드가 아니라 새 탭 이동이므로 버튼 표시를 구분한다.
  const isExternalLink = downloadHref ? /^https?:\/\//.test(downloadHref) : false;
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

      {collectedUrl ? (
        <SourceContentView contentUrl={collectedUrl} />
      ) : (
        <div
          role="img"
          aria-label={`${node.name} 미리보기`}
          className="flex aspect-[4/3] items-center justify-center rounded border border-dashed border-border bg-bg-secondary text-xs text-fg-tertiary"
        >
          미리보기 준비 중 - PDF·HWPX 1페이지 썸네일로 표시됩니다
        </div>
      )}

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
        {downloadHref ? (
          <Button variant="outline" onClick={() => downloadFile(downloadHref, node.name)}>
            {isExternalLink ? (
              <>
                <ArrowUpRight className="mr-1 h-4 w-4" />
                원문 링크
              </>
            ) : (
              <>
                <Download className="mr-1 h-4 w-4" />
                다운로드
              </>
            )}
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

function SourceContentView({ contentUrl }: { contentUrl: string }) {
  const { data, isLoading, isError, error } = useSourceContent(contentUrl);
  return (
    <section className="rounded border border-border bg-bg">
      <header className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
        <span className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-fg-tertiary">
          <FileText className="h-3.5 w-3.5" aria-hidden />
          수집 원문
        </span>
        {data ? (
          <span className="font-mono text-xs text-fg-tertiary">
            {data.char_count.toLocaleString()}자 · {formatSize(data.byte_count)}
          </span>
        ) : null}
      </header>
      <div className="max-h-[32rem] overflow-y-auto px-4 py-3">
        {isLoading ? (
          <p className="py-8 text-center text-sm text-fg-tertiary">원문을 불러오는 중…</p>
        ) : isError ? (
          <p className="py-8 text-center text-sm text-fg-danger">
            {error instanceof ApiError ? error.message : "원문을 불러오지 못했습니다."}
          </p>
        ) : data?.content_md.trim() ? (
          <MarkdownContent content={data.content_md} />
        ) : (
          <p className="py-8 text-center text-sm text-fg-tertiary">표시할 본문이 없습니다.</p>
        )}
      </div>
    </section>
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
