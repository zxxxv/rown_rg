import { LogOut, Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";
import { Link, Outlet, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import type { UserRole } from "@/components/auth/RequireAuth";
import { Sidebar } from "@/components/layout/Sidebar";
import { Button } from "@/components/ui/button";

export interface AppShellProps {
  user: { name: string; role: UserRole } | null;
  tokenUsage?: { used: number; limit: number };
  onLogout?: () => void;
  children?: ReactNode;
}

export function AppShell({ user, tokenUsage, onLogout, children }: AppShellProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [params] = useSearchParams();
  const role = user?.role ?? "viewer";
  const demoMode = params.get("demo") === "1";

  return (
    <div className="flex min-h-screen bg-bg">
      <Sidebar
        role={role}
        collapsed={collapsed}
        onToggleCollapsed={() => setCollapsed((v) => !v)}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-between border-b border-border bg-bg px-6">
          <div className="text-sm text-fg-tertiary">AI 보고서 자동생성 시스템</div>
          <div className="flex items-center gap-4">
            {demoMode ? (
              <button
                type="button"
                onClick={() =>
                  toast("발표 모드 활성", {
                    description: "가속 시나리오 + 시연용 데이터를 사용합니다.",
                  })
                }
                className="inline-flex items-center gap-1 rounded-sm border border-border-info bg-bg-info px-2 py-1 text-xs font-medium text-fg-info hover:bg-bg-info/80"
              >
                <Sparkles className="h-3 w-3" aria-hidden />
                발표 모드
              </button>
            ) : null}

            {tokenUsage ? (
              <div className="flex items-center gap-2 font-mono text-xs text-fg-secondary">
                <span>토큰</span>
                <span className="text-fg">
                  {tokenUsage.used.toLocaleString()} / {tokenUsage.limit.toLocaleString()}
                </span>
              </div>
            ) : null}

            {user ? (
              <div className="flex items-center gap-2">
                <Link
                  to="/profile"
                  className="flex items-center gap-2 rounded-full py-0.5 pl-0.5 pr-2 transition-colors hover:bg-bg-tertiary"
                  aria-label="마이페이지"
                >
                  <span
                    aria-hidden
                    className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-accent font-mono text-xs text-accent-foreground"
                  >
                    {user.name.slice(0, 1)}
                  </span>
                  <span className="text-sm text-fg">{user.name}</span>
                </Link>
                {onLogout ? (
                  <Button variant="ghost" size="sm" onClick={onLogout} aria-label="로그아웃">
                    <LogOut className="h-4 w-4" />
                  </Button>
                ) : null}
              </div>
            ) : null}
          </div>
        </header>

        <main className="flex-1 px-8 py-6">{children ?? <Outlet />}</main>
      </div>
    </div>
  );
}
