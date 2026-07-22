import {
  Activity,
  ChevronLeft,
  ChevronRight,
  FolderTree,
  Globe,
  KeyRound,
  Library,
  Loader2,
  ShieldCheck,
  SquareStack,
  Users,
} from "lucide-react";
import { NavLink } from "react-router-dom";
import { useProjectList } from "@/api/projects";
import type { UserRole } from "@/components/auth/RequireAuth";
import { cn } from "@/lib/utils";

interface NavItem {
  to: string;
  label: string;
  icon: typeof FolderTree;
  minRole?: UserRole;
  disabled?: boolean;
  phaseBadge?: string;
}

const MAIN_NAV: NavItem[] = [
  { to: "/projects", label: "프로젝트", icon: FolderTree },
  { to: "/library", label: "라이브러리", icon: Library },
];

const ADMIN_NAV: NavItem[] = [
  { to: "/admin/dashboard", label: "대시보드", icon: ShieldCheck, minRole: "admin" },
  { to: "/admin/users", label: "사용자", icon: Users, minRole: "admin" },
  {
    to: "/admin/tokens",
    label: "토큰 한도",
    icon: Activity,
    minRole: "admin",
    disabled: true,
    phaseBadge: "준비 중",
  },
  { to: "/admin/ip", label: "IP 관리", icon: Globe, minRole: "super_admin" },
  {
    to: "/admin/hwpx-style",
    label: "HWPX 양식",
    icon: SquareStack,
    minRole: "admin",
    disabled: true,
    phaseBadge: "준비 중",
  },
];

const ROLE_RANK: Record<UserRole, number> = {
  viewer: 1,
  worker: 2,
  admin: 3,
  super_admin: 4,
};

function visible(role: UserRole, item: NavItem): boolean {
  if (!item.minRole) return true;
  return ROLE_RANK[role] >= ROLE_RANK[item.minRole];
}

export interface SidebarProps {
  role: UserRole;
  collapsed: boolean;
  onToggleCollapsed: () => void;
}

export function Sidebar({ role, collapsed, onToggleCollapsed }: SidebarProps) {
  // 서버 status 필터 사용 — 작성 중(writing) 최신 3건
  const activeQuery = useProjectList({ status: "writing", limit: 3 });
  const activeProjects = activeQuery.data ?? [];
  const showAdmin = ROLE_RANK[role] >= ROLE_RANK.admin;

  return (
    <aside
      className={cn(
        "flex flex-col border-r border-border bg-bg-secondary transition-[width] duration-200",
        collapsed ? "w-16" : "w-60",
      )}
    >
      <div
        className={cn(
          "flex h-14 items-center border-b border-border px-4",
          collapsed ? "justify-center" : "justify-between",
        )}
      >
        {!collapsed && <span className="text-sm font-semibold text-fg">로운인사이트</span>}
        <button
          type="button"
          onClick={onToggleCollapsed}
          className="inline-flex h-7 w-7 items-center justify-center rounded-sm text-fg-secondary hover:bg-bg-tertiary"
          aria-label={collapsed ? "사이드바 펼치기" : "사이드바 접기"}
        >
          {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
        </button>
      </div>

      <nav className="flex flex-1 flex-col gap-1 overflow-y-auto p-2">
        {MAIN_NAV.filter((n) => visible(role, n)).map((item) => (
          <SideLink key={item.to} item={item} collapsed={collapsed} />
        ))}

        {!collapsed && activeProjects.length > 0 ? (
          <div className="mt-3 flex flex-col gap-1 border-t border-border pt-3">
            <div className="flex items-center justify-between px-3 pb-1">
              <span className="text-[10px] font-medium uppercase tracking-wide text-fg-tertiary">
                진행 중
              </span>
              {activeQuery.isFetching ? (
                <Loader2 className="h-3 w-3 animate-spin text-fg-tertiary" aria-hidden />
              ) : null}
            </div>
            {activeProjects.map((p) => (
              <NavLink
                key={p.id}
                to={`/projects/${p.id}/overview`}
                className={({ isActive }) =>
                  cn(
                    "flex flex-col gap-1 rounded px-3 py-1.5 text-xs transition-colors",
                    isActive
                      ? "bg-bg-tertiary text-fg"
                      : "text-fg-secondary hover:bg-bg-tertiary hover:text-fg",
                  )
                }
              >
                <span className="line-clamp-1">{p.title}</span>
                <div className="h-0.5 w-full overflow-hidden rounded-full bg-bg-tertiary">
                  <div
                    className="h-full bg-accent transition-[width]"
                    style={{ width: `${p.progress ?? 0}%` }}
                  />
                </div>
              </NavLink>
            ))}
          </div>
        ) : null}

        {showAdmin ? (
          <div className="mt-3 flex flex-col gap-1 border-t border-border pt-3">
            {!collapsed ? (
              <div className="flex items-center gap-2 px-3 pb-1">
                <KeyRound className="h-3 w-3 text-fg-tertiary" aria-hidden />
                <span className="text-[10px] font-medium uppercase tracking-wide text-fg-tertiary">
                  관리자
                </span>
              </div>
            ) : null}
            {ADMIN_NAV.filter((n) => visible(role, n)).map((item) => (
              <SideLink key={item.to} item={item} collapsed={collapsed} />
            ))}
          </div>
        ) : null}
      </nav>
    </aside>
  );
}

function SideLink({ item, collapsed }: { item: NavItem; collapsed: boolean }) {
  if (item.disabled) {
    return (
      <span
        className={cn(
          "inline-flex cursor-not-allowed items-center gap-3 rounded px-3 py-2 text-sm text-fg-tertiary opacity-60",
        )}
        aria-disabled
      >
        <item.icon className="h-4 w-4 shrink-0" />
        {!collapsed ? (
          <>
            <span className="flex-1">{item.label}</span>
            {item.phaseBadge ? (
              <span className="rounded-sm border border-border bg-bg-secondary px-1.5 py-0.5 text-[9px] text-fg-tertiary">
                {item.phaseBadge}
              </span>
            ) : null}
          </>
        ) : null}
      </span>
    );
  }
  return (
    <NavLink
      to={item.to}
      end={item.to === "/projects"}
      className={({ isActive }) =>
        cn(
          "inline-flex items-center gap-3 rounded px-3 py-2 text-sm transition-colors",
          isActive
            ? "bg-bg-tertiary text-fg"
            : "text-fg-secondary hover:bg-bg-tertiary hover:text-fg",
        )
      }
    >
      <item.icon className="h-4 w-4 shrink-0" />
      {!collapsed && <span>{item.label}</span>}
    </NavLink>
  );
}
