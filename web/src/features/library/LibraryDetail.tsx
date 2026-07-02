import {
  ArrowUpRight,
  Download,
  FilePlus2,
  FileText,
  FolderOpen,
  Settings,
  Shield,
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { useProject } from "@/api/projects";
import type { LibraryNode, SourceKind } from "@/api/types";
import { EmptyState } from "@/components/feedback/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

interface LibraryDetailProps {
  node: LibraryNode | null;
  path: string[];
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

export function LibraryDetail({ node, path }: LibraryDetailProps) {
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

      {node.type === "folder" ? <FolderBody node={node} /> : <FileBody node={node} />}
    </article>
  );
}

function Breadcrumb({ path }: { path: string[] }) {
  if (path.length === 0) {
    return <p className="font-mono text-xs text-fg-tertiary">/</p>;
  }
  return <p className="font-mono text-xs text-fg-tertiary">/ {path.join(" / ")}</p>;
}

function FolderBody({ node }: { node: Extract<LibraryNode, { type: "folder" }> }) {
  const stats = countDescendants(node);
  const files = node.children.filter((c) => c.type === "file");
  const subfolders = node.children.filter((c) => c.type === "folder");

  return (
    <div className="flex flex-col gap-4">
      <dl className="grid grid-cols-3 gap-3 rounded border border-border bg-bg-secondary p-3 font-mono text-sm">
        <Stat label="하위 폴더" value={`${stats.folders}개`} />
        <Stat label="하위 파일" value={`${stats.files}개`} />
        <Stat label="총 크기" value={formatSize(stats.bytes)} />
      </dl>

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
      </div>

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
            <Button variant="outline" onClick={() => toast("파일 업로드 — 구현 예정")}>
              파일 업로드
            </Button>
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
        <Button variant="outline" onClick={() => toast(`다운로드 — 구현 예정 (${node.name})`)}>
          <Download className="mr-1 h-4 w-4" />
          다운로드
        </Button>
        <Button variant="ghost" onClick={() => toast("권한 설정 — 구현 예정")}>
          <Settings className="mr-1 h-4 w-4" />
          권한 설정
        </Button>
      </footer>

      <p className="flex items-center gap-1 text-xs text-fg-tertiary">
        <FileText className="h-3 w-3" aria-hidden />
        파일 ID: <span className="font-mono">{node.id}</span>
      </p>
    </div>
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
