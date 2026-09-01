import { LogOut, Menu, Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";
import { Link, Outlet, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { useMyTokenUsage } from "@/api/profile";
import type { UserRole } from "@/components/auth/RequireAuth";
import { Sidebar } from "@/components/layout/Sidebar";
import { UsageMeter } from "@/components/layout/UsageMeter";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from "@/components/ui/sheet";

export interface AppShellProps {
  user: { name: string; role: UserRole } | null;
  onLogout?: () => void;
  children?: ReactNode;
}

export function AppShell({ user, onLogout, children }: AppShellProps) {
  // 접기는 사용자가 정한다 - 좁은 화면에서 자동으로 접으면 라벨이 사라져 원하는
  // 모습이 아니게 된다(2026-08-10). 폭 절약은 사이드바 자체를 좁혀서 한다.
  const [collapsed, setCollapsed] = useState(false);
  // 폰에서는 사이드바가 화면의 2/3를 먹어 본문이 짓눌린다 - 서랍으로 접는다.
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [params] = useSearchParams();
  const role = user?.role ?? "viewer";
  const demoMode = params.get("demo") === "1";
  // 화면에 박아둔 숫자(1,240,000 / 5,000,000)는 아무 데이터도 아니었다 - 이번 달
  // 실사용량을 직접 읽는다(5분 캐시라 페이지마다 요청이 늘지 않는다).
  const usage = useMyTokenUsage().data;

  return (
    <div className="flex min-h-screen bg-bg">
      {/* md 미만에서는 숨기고 헤더의 메뉴 버튼으로 연다(아래 Sheet). */}
      <div className="hidden md:flex">
        <Sidebar
          role={role}
          collapsed={collapsed}
          onToggleCollapsed={() => setCollapsed((v) => !v)}
        />
      </div>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-between gap-2 border-b border-border bg-bg px-4 sm:px-6">
          <div className="flex min-w-0 items-center gap-2">
            <Sheet open={drawerOpen} onOpenChange={setDrawerOpen}>
              <SheetTrigger asChild>
                <button
                  type="button"
                  className="inline-flex h-9 w-9 items-center justify-center rounded-sm text-fg-secondary hover:bg-bg-tertiary md:hidden"
                  aria-label="메뉴 열기"
                >
                  <Menu className="h-5 w-5" aria-hidden />
                </button>
              </SheetTrigger>
              <SheetContent side="left" className="w-64 p-0">
                <SheetTitle className="sr-only">메뉴</SheetTitle>
                {/* 서랍 안에서는 항상 펼친 상태 - 좁은 화면에서 아이콘만 남기면 무엇인지
                    모른다. 이동하면 스스로 닫아야 본문이 보인다. */}
                <Sidebar role={role} collapsed={false} onNavigate={() => setDrawerOpen(false)} />
              </SheetContent>
            </Sheet>
            <div className="truncate text-sm text-fg-tertiary">AI 보고서 자동생성 시스템</div>
          </div>
          {/* shrink-0: 좁은 폭에서 이쪽이 짓눌리면 한글·숫자가 한 글자씩 세로로
              꺾인다(2026-08-20 폰 실측). 양보는 왼쪽 제목의 truncate가 한다. */}
          <div className="flex shrink-0 items-center gap-2 sm:gap-4">
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

            {usage ? <UsageMeter usage={usage} /> : null}

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
                  {/* 이름이 아주 길면 여기가 다시 헤더를 밀어낸다 - 폰에서는 상한을 두고
                      말줄임. 전체 이름은 마이페이지에서 본다. */}
                  <span className="max-w-[6rem] truncate whitespace-nowrap text-sm text-fg sm:max-w-none">
                    {user.name}
                  </span>
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

        {/* 초광폭에서 본문이 끝없이 늘어나면 읽기 어렵다 - 상한을 두고 가운데 정렬.
            노트북 폭(1280~1600)에서는 상한에 안 걸려 폭을 그대로 다 쓴다. */}
        <main className="min-w-0 flex-1 px-4 py-4 sm:px-6 sm:py-6 lg:px-8">
          <div className="mx-auto w-full max-w-[1600px]">{children ?? <Outlet />}</div>
        </main>
      </div>
    </div>
  );
}
