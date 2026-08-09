import { Lock } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";

export default function ForbiddenPage() {
  const navigate = useNavigate();
  return (
    <main className="flex min-h-screen items-center justify-center bg-bg-secondary px-4 py-12">
      <div className="flex w-full max-w-md flex-col items-center gap-4 rounded-lg border border-border bg-bg p-8 text-center">
        <div
          aria-hidden
          className="inline-flex h-14 w-14 items-center justify-center rounded-full bg-bg-danger text-fg-danger"
        >
          <Lock className="h-7 w-7" />
        </div>
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-semibold text-fg">접근 권한이 없습니다</h1>
          <p className="text-sm text-fg-secondary">
            관리자 권한이 필요한 페이지입니다. 권한이 필요하면 운영팀에 문의해 주세요.
          </p>
        </div>
        <div className="flex flex-wrap items-center justify-center gap-2 pt-2">
          <Button onClick={() => navigate("/projects")}>프로젝트로 돌아가기</Button>
          <Button variant="ghost" onClick={() => navigate(-1)}>
            이전으로
          </Button>
        </div>
      </div>
    </main>
  );
}
