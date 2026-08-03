import { ArrowLeft } from "lucide-react";
import { useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { parseQaSelectPayload } from "@/api/checkpoints";
import { useProgressSnapshot } from "@/api/progress";
import { useProject } from "@/api/projects";
import { EmptyState } from "@/components/feedback/EmptyState";
import { PageLoading } from "@/components/feedback/PageLoading";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { QaSelectGate } from "@/features/progress/QaSelectGate";
import { useAuth } from "@/hooks/useAuth";

/** QA 후보 선택 전용 화면 — 자료 검토와 같은 패턴의 결정 작업 공간.
 *
 * 게이트 payload는 진행 스냅샷(pending_gate)에서 읽는다. 게이트가 열려 있지
 * 않으면 대기 안내만 보여준다(작성이 끝나면 폴링이 자동으로 활성화한다).
 */
export default function QaSelectPage() {
  const { id: projectId = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { user, logout } = useAuth();

  const project = useProject(projectId).data;
  const snapshotQuery = useProgressSnapshot(projectId, true, { refetchInterval: 7_000 });

  const payload = useMemo(
    () =>
      snapshotQuery.data?.pending_gate?.gate === "qa_select"
        ? parseQaSelectPayload(snapshotQuery.data.pending_gate.payload)
        : null,
    [snapshotQuery.data],
  );

  return (
    <AppShell
      user={user ? { name: user.name, role: user.role } : null}
      onLogout={() => void logout()}
      tokenUsage={{ used: 1_240_000, limit: 5_000_000 }}
    >
      <div className="flex flex-col gap-6">
        <header className="flex flex-col gap-3">
          <Button
            variant="ghost"
            size="sm"
            className="w-fit text-fg-secondary"
            onClick={() => navigate(`/projects/${projectId}/overview`)}
          >
            <ArrowLeft className="mr-1 h-4 w-4" />
            프로젝트 개요
          </Button>
          <div className="flex items-center gap-2">
            <h1 className="text-3xl font-semibold text-fg">QA 후보 선택</h1>
            <Badge variant="secondary" className="font-mono">
              검토 지점 #2
            </Badge>
          </div>
          {project ? (
            <p className="text-sm text-fg-secondary">
              {project.title} — 섹션별 초안 후보 중 보고서에 실을 것을 하나씩 골라 제출하면 조립이
              재개됩니다.
            </p>
          ) : null}
        </header>

        {snapshotQuery.isLoading ? (
          <PageLoading label="진행 상태를 불러오는 중…" />
        ) : payload ? (
          <QaSelectGate
            projectId={projectId}
            payload={payload}
            onResumed={() => {
              toast.success("보고서 조립을 재개합니다.");
              navigate(`/projects/${projectId}/overview`);
            }}
          />
        ) : (
          <EmptyState
            title="후보 선택 대기 상태가 아닙니다"
            description="본문 작성이 끝나면 이 화면이 자동으로 활성화됩니다. 진행 위치는 개요의 진행 단계에서 확인하세요."
            action={
              <Button variant="outline" onClick={() => navigate(`/projects/${projectId}/overview`)}>
                개요로 이동
              </Button>
            }
          />
        )}
      </div>
    </AppShell>
  );
}
