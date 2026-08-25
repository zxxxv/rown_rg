import { AlertCircle, CircleCheck, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { FormIssue } from "./validation";

// ─── 생성 준비 상태 - "무엇이 빠져서 프로젝트 생성이 안 되나"를 한 곳에서 답한다 ───
// 종전에는 제출 버튼을 누른 뒤에야 토스트 한 줄로 알았고, 목차 쪽 사유(없는 절
// 참조·절 수 초과)는 서버 422 문구가 전부라 어느 절인지 알 수 없었다. 이 패널은
// 타이핑하는 동안 계속 갱신되고, 항목마다 그 칸으로 데려간다(2026-08-25).

export interface ReadinessPanelProps {
  issues: FormIssue[];
  /** 항목을 눌렀을 때 해당 칸으로 이동(장 펼치기 + 스크롤 + 포커스) */
  onJump: (issue: FormIssue) => void;
  /** 수정 모드처럼 좁은 곳에 넣을 때 - 여백을 줄이고 경고는 접어 둔다 */
  compact?: boolean;
}

export function ReadinessPanel({ issues, onJump, compact }: ReadinessPanelProps) {
  const blockers = issues.filter((i) => i.level === "blocker");
  const warnings = issues.filter((i) => i.level === "warning");

  return (
    <div
      id="pf-readiness"
      className={cn(
        "flex flex-col gap-2 rounded border bg-bg",
        compact ? "p-3" : "p-4",
        blockers.length > 0 ? "border-fg-danger/50" : "border-border",
      )}
    >
      <div className="flex items-center gap-1.5">
        {blockers.length > 0 ? (
          <>
            <AlertCircle className="h-4 w-4 shrink-0 text-fg-danger" aria-hidden />
            <p className="text-xs font-medium text-fg-danger">
              생성하려면 {blockers.length}가지를 고쳐야 합니다
            </p>
          </>
        ) : (
          <>
            <CircleCheck className="h-4 w-4 shrink-0 text-fg-success" aria-hidden />
            <p className="text-xs font-medium text-fg-success">필수 항목을 다 채웠습니다</p>
          </>
        )}
      </div>

      {blockers.length > 0 ? (
        <ul className="flex flex-col gap-1.5">
          {blockers.map((issue) => (
            <IssueRow key={issue.id} issue={issue} onJump={onJump} />
          ))}
        </ul>
      ) : null}

      {warnings.length > 0 ? (
        <details className="group" open={!compact && blockers.length === 0}>
          <summary className="flex cursor-pointer select-none items-center gap-1.5 text-xs text-fg-warning">
            <TriangleAlert className="h-3.5 w-3.5 shrink-0" aria-hidden />
            확인해 볼 것 {warnings.length}가지 (없어도 생성됩니다)
          </summary>
          <ul className="mt-1.5 flex flex-col gap-1.5">
            {warnings.map((issue) => (
              <IssueRow key={issue.id} issue={issue} onJump={onJump} />
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function IssueRow({ issue, onJump }: { issue: FormIssue; onJump: (issue: FormIssue) => void }) {
  return (
    <li
      className={cn(
        "flex items-start gap-2 rounded-sm px-2 py-1.5",
        issue.level === "blocker" ? "bg-bg-danger" : "bg-bg-warning",
      )}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span
          className={cn(
            "text-[11px] font-medium",
            issue.level === "blocker" ? "text-fg-danger" : "text-fg-warning",
          )}
        >
          {issue.where}
          {issue.more ? <span className="font-normal"> 외 {issue.more}곳</span> : null}
        </span>
        <span className="text-[11px] leading-relaxed text-fg-secondary">{issue.message}</span>
      </div>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-6 shrink-0 px-1.5 text-[11px] text-fg-secondary"
        onClick={() => onJump(issue)}
      >
        이동
      </Button>
    </li>
  );
}
