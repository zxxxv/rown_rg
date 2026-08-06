import { Activity } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { QuotaSettingsCard } from "@/features/admin/QuotaSettingsCard";
import { useAuth } from "@/hooks/useAuth";

/**
 * 토큰 한도(정책) — 조직 월 상한 + 역할별 기본 한도.
 *
 * 관리자 섹션 역할 분리: 대시보드=집계(보기), 사용자=개별 관리(한도 조정 포함),
 * 토큰 한도=정책(여기). 개별 사용자 한도는 대시보드에서 사용자를 클릭해 조정한다.
 */
export default function AdminTokensPage() {
  const { user, logout } = useAuth();
  return (
    <AppShell
      user={user ? { name: user.name, role: user.role } : null}
      onLogout={() => void logout()}
    >
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
        <header>
          <h1 className="flex items-center gap-2 text-3xl font-semibold text-fg">
            <Activity className="h-7 w-7 text-fg-secondary" aria-hidden />
            조직 한도
          </h1>
          <p className="text-sm text-fg-secondary">
            조직 월 비용 상한과 역할별 기본 한도(정책)를 설정합니다. 개별 사용자 한도는 대시보드에서
            사용자를 클릭해 조정하세요.
          </p>
        </header>
        <QuotaSettingsCard />
      </div>
    </AppShell>
  );
}
