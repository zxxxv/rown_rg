import { Calendar } from "lucide-react";
import type { Project, ProjectStatus } from "@/api/types";
import { StatusDot, type StatusKind } from "@/components/data-display/StatusDot";
import { Badge } from "@/components/ui/badge";
import { presetLabel } from "@/features/project-config/presets";
import { cn } from "@/lib/utils";

const STATUS_LABEL: Record<ProjectStatus, string> = {
  created: "생성됨",
  researching: "자료 수집",
  indexing: "인덱싱",
  writing: "작성 중",
  reviewing: "검토 대기",
  completed: "완료",
  archived: "보관",
};

const STATUS_KIND: Record<ProjectStatus, StatusKind> = {
  created: "tertiary",
  researching: "info",
  indexing: "info",
  writing: "info",
  reviewing: "warning",
  completed: "success",
  archived: "tertiary",
};

export interface ProjectCardProps {
  project: Project;
  onClick?: () => void;
  className?: string;
}

export function ProjectCard({ project, onClick, className }: ProjectCardProps) {
  const interactive = Boolean(onClick);
  const progress = Math.max(0, Math.min(100, project.progress ?? 0));

  return (
    <article
      className={cn(
        "flex flex-col gap-3 rounded border border-border bg-bg p-5 transition-colors",
        interactive && "cursor-pointer hover:border-border-strong hover:bg-bg-secondary",
        className,
      )}
      onClick={onClick}
      onKeyDown={(e) => {
        if (!onClick) return;
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }}
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
    >
      <header className="flex items-center justify-between gap-2">
        <Badge variant="secondary" className="font-mono text-xs">
          {presetLabel(project.preset)}
        </Badge>
        <StatusDot kind={STATUS_KIND[project.status]} label={STATUS_LABEL[project.status]} />
      </header>

      <h3 className="line-clamp-2 text-base font-semibold text-fg">{project.title}</h3>
      <p className="line-clamp-2 text-sm text-fg-secondary">{project.topic}</p>

      <div className="flex flex-col gap-1">
        <div className="flex items-center justify-between text-xs text-fg-tertiary">
          <span>진행률</span>
          <span className="font-mono text-fg-secondary">{progress}%</span>
        </div>
        <div
          className="h-1.5 w-full overflow-hidden rounded-full bg-bg-tertiary"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progress}
        >
          <div className="h-full bg-accent transition-[width]" style={{ width: `${progress}%` }} />
        </div>
      </div>

      <footer className="flex items-center gap-1.5 pt-1 text-xs text-fg-tertiary">
        <Calendar className="h-3.5 w-3.5" aria-hidden />
        <span className="font-mono">생성일 {project.created_at.slice(0, 10)}</span>
      </footer>
    </article>
  );
}
