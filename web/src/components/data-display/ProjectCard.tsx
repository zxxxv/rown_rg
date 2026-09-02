import { Calendar } from "lucide-react";
import type { Project, ProjectStatus } from "@/api/types";
import { StatusDot, type StatusKind } from "@/components/data-display/StatusDot";
import { Badge } from "@/components/ui/badge";
import { presetLabel } from "@/features/project-config/presets";
import { cn } from "@/lib/utils";

const STATUS_LABEL: Record<ProjectStatus, string> = {
  created: "생성됨",
  planning: "설계 검토",
  researching: "자료 수집",
  indexing: "인덱싱",
  writing: "작성 중",
  reviewing: "검토 중",
  completed: "완료",
  archived: "보관",
  cancelled: "취소됨",
};

const STATUS_KIND: Record<ProjectStatus, StatusKind> = {
  created: "tertiary",
  planning: "warning",
  researching: "info",
  indexing: "info",
  writing: "info",
  reviewing: "warning",
  completed: "success",
  archived: "tertiary",
  cancelled: "danger",
};

// 단계 기반 근사 진행률 - 백엔드 _STAGE_PERCENT와 동일. ProjectRead에 progress가 없어
// (실백엔드) 상태에서 유도한다. 완료·보관은 100%.
const STATUS_PERCENT: Record<ProjectStatus, number> = {
  created: 0,
  planning: 5,
  researching: 20,
  indexing: 40,
  writing: 60,
  reviewing: 85,
  completed: 100,
  archived: 100,
  cancelled: 0,
};

export interface ProjectCardProps {
  project: Project;
  onClick?: () => void;
  className?: string;
}

export function ProjectCard({ project, onClick, className }: ProjectCardProps) {
  const interactive = Boolean(onClick);
  // 목록 카드는 프로젝트 상태가 진실(완료=100%). project.progress는 실백엔드엔 없음.
  const progress = Math.max(0, Math.min(100, project.progress ?? STATUS_PERCENT[project.status]));
  // 조립이 끝났어도 **확정 전이면 "검토 중"**이다 - 목록 필터가 그 기준으로 가르므로
  // (완료 칸 = finalized_at 있는 것만) 배지가 다르게 말하면 같은 화면이 서로 어긋난다:
  // 진행 중 칸에 "완료" 배지가 달린 카드가 놓인다(2026-08-26 배포 점검에서 발견).
  const unsigned = project.status === "completed" && !project.finalized_at;
  const label = unsigned ? "검토 중" : STATUS_LABEL[project.status];
  const kind = unsigned ? "warning" : STATUS_KIND[project.status];

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
          {presetLabel(project.preset, project.preset_name)}
        </Badge>
        <StatusDot kind={kind} label={label} />
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
