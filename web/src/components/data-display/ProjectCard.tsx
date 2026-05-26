import { CalendarClock } from "lucide-react";
import type { Preset, Project, ProjectStatus } from "@/api/types";
import { StatusDot, type StatusKind } from "@/components/data-display/StatusDot";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const STATUS_LABEL: Record<ProjectStatus, string> = {
  draft: "초안",
  researching: "자료조사",
  indexing: "인덱싱",
  writing: "작성 중",
  qa: "QA",
  review: "검토 대기",
  completed: "완료",
  archived: "보관",
  failed: "실패",
};

const STATUS_KIND: Record<ProjectStatus, StatusKind> = {
  draft: "tertiary",
  researching: "info",
  indexing: "info",
  writing: "info",
  qa: "info",
  review: "warning",
  completed: "success",
  archived: "tertiary",
  failed: "danger",
};

const PRESET_LABEL: Record<Preset, string> = {
  preliminary_feasibility: "예비타당성",
  business_review: "사업타당성",
  policy_research: "정책연구",
  blank: "빈 양식",
};

type DeadlineKind = "none" | "future" | "near" | "overdue";

function deadlineKind(deadline: string | undefined): DeadlineKind {
  if (!deadline) return "none";
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const target = new Date(deadline);
  const diffDays = Math.floor((target.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));
  if (diffDays < 0) return "overdue";
  if (diffDays <= 7) return "near";
  return "future";
}

const DEADLINE_CLASS: Record<DeadlineKind, string> = {
  none: "text-fg-tertiary",
  future: "text-fg-secondary",
  near: "text-fg-warning",
  overdue: "text-fg-danger",
};

export interface ProjectCardProps {
  project: Project;
  onClick?: () => void;
  className?: string;
}

export function ProjectCard({ project, onClick, className }: ProjectCardProps) {
  const interactive = Boolean(onClick);
  const progress = Math.max(0, Math.min(100, project.progress ?? 0));
  const dlKind = deadlineKind(project.deadline);

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
          {PRESET_LABEL[project.preset]}
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

      <footer className="flex items-center gap-1.5 pt-1 text-xs">
        <CalendarClock className={cn("h-3.5 w-3.5", DEADLINE_CLASS[dlKind])} aria-hidden />
        <span className={cn("font-mono", DEADLINE_CLASS[dlKind])}>
          {project.deadline ? `마감 ${project.deadline}` : "마감일 미설정"}
          {dlKind === "near" ? " · 임박" : dlKind === "overdue" ? " · 지남" : ""}
        </span>
      </footer>
    </article>
  );
}
