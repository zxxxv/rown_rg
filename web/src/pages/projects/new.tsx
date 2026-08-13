import { ArrowLeft, Lock } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { useCreateProject, useRunProject } from "@/api/projects";
import { EmptyState } from "@/components/feedback/EmptyState";
import { AppShell } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/button";
import { ProjectConfigForm } from "@/features/project-config/ProjectConfigForm";
import type { ProjectFormValues } from "@/features/project-config/schema";
import { useAuth } from "@/hooks/useAuth";

export default function NewProjectPage() {
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const createProject = useCreateProject();
  const runProject = useRunProject();

  // 뷰어는 열람 전용 - URL 직접 진입도 폼 대신 사유를 보여준다(백엔드도 403으로 막는다).
  if (user?.role === "viewer") {
    return (
      <AppShell user={{ name: user.name, role: user.role }} onLogout={() => void logout()}>
        <EmptyState
          icon={Lock}
          title="프로젝트 생성 권한이 필요합니다"
          description="지금 계정은 열람 전용(viewer)입니다 - 관리자에게 권한 상향을 문의하세요."
          action={
            <Button variant="outline" onClick={() => navigate("/projects")}>
              프로젝트 목록으로
            </Button>
          }
        />
      </AppShell>
    );
  }

  const handleSubmit = async (values: ProjectFormValues) => {
    try {
      const project = await createProject.mutateAsync(values);
      toast.success("새 프로젝트가 생성됐습니다.", { description: project.title });
      try {
        // 생성 직후 백그라운드 실행 시작 → 진행 화면으로 이동
        await runProject.mutateAsync(project.id);
      } catch (runErr) {
        const msg = runErr instanceof ApiError ? runErr.message : "실행 시작에 실패했습니다.";
        toast.error("실행 시작 실패", {
          description: `${msg} - 개요 화면에서 다시 시작할 수 있습니다.`,
        });
      }
      // 개요의 진행 단계 스테퍼가 실행을 지켜보는 화면이다(진행 페이지는 폐지됨).
      navigate(`/projects/${project.id}/overview`, { replace: true });
    } catch (err) {
      if (err instanceof ApiError) {
        // 에러코드는 개발자용 - 사용자에겐 백엔드가 준 사람이 읽을 이유(message)만 보여준다.
        const title =
          err.status === 422
            ? "입력값을 다시 확인해 주세요."
            : err.status === 429
              ? "한도 초과 - 프로젝트를 만들 수 없습니다"
              : "프로젝트 생성 실패";
        toast.error(title, { description: err.message });
      } else {
        toast.error("프로젝트 생성 중 알 수 없는 오류가 발생했습니다.");
      }
    }
  };

  return (
    <AppShell
      user={user ? { name: user.name, role: user.role } : null}
      onLogout={() => void logout()}
    >
      <div className="flex flex-col gap-6">
        <header className="flex flex-col gap-2">
          <Button
            variant="ghost"
            size="sm"
            className="w-fit text-fg-secondary"
            onClick={() => navigate("/projects")}
          >
            <ArrowLeft className="mr-1 h-4 w-4" />
            프로젝트 목록
          </Button>
          <h1 className="text-3xl font-semibold text-fg">새 프로젝트</h1>
          <p className="text-sm text-fg-secondary">
            보고서 유형을 고르고 목차(장·절)를 직접 확정하면, 그 목차 순서로 자료 조사가 시작됩니다.
            수집된 자료는 검토 단계에서 확정·제외할 수 있습니다.
          </p>
        </header>

        <ProjectConfigForm
          mode="create"
          onSubmit={handleSubmit}
          onCancel={() => navigate("/projects")}
          submitting={createProject.isPending || runProject.isPending}
        />
      </div>
    </AppShell>
  );
}
