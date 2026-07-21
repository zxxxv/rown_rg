import { ArrowLeft } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { useCreateProject, useRunProject } from "@/api/projects";
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
          description: `${msg} — 개요 화면에서 다시 시작할 수 있습니다.`,
        });
      }
      navigate(`/projects/${project.id}/progress`, { replace: true });
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 422) {
          toast.error("입력값을 다시 확인해 주세요.", { description: err.message });
        } else {
          toast.error("프로젝트 생성 실패", {
            description: `${err.code} · ${err.message}`,
          });
        }
      } else {
        toast.error("프로젝트 생성 중 알 수 없는 오류가 발생했습니다.");
      }
    }
  };

  return (
    <AppShell
      user={user ? { name: user.name, role: user.role } : null}
      onLogout={() => void logout()}
      tokenUsage={{ used: 1_240_000, limit: 5_000_000 }}
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
            프리셋을 고른 뒤 7개 영역의 옵션을 검토하고 작성을 시작하세요. 옵션은 작성 도중에도 일부
            변경할 수 있습니다.
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
