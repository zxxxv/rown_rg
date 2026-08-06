import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/hooks/useAuth";

export function AuthDemo() {
  const { user, isLoading, login, logout } = useAuth();
  const [loginId, setLoginId] = useState("admin_rown");
  const [password, setPassword] = useState("Admin_rown12!");
  const [busy, setBusy] = useState(false);

  const onLogin = async () => {
    setBusy(true);
    try {
      const u = await login({ login_id: loginId, password });
      toast.success(`로그인 성공 - ${u.name}`);
    } catch (err) {
      if (err instanceof ApiError) {
        toast.error(`로그인 실패 (${err.status ?? "?"})`, { description: err.message });
      } else {
        toast.error("로그인 중 알 수 없는 오류");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-4 rounded border border-border bg-bg p-5">
      <div className="text-sm text-fg-secondary">
        현재 상태:{" "}
        {isLoading ? (
          <span>불러오는 중…</span>
        ) : user ? (
          <span className="text-fg">
            로그인됨 · <span className="font-mono">{user.email}</span> ({user.role})
          </span>
        ) : (
          <span className="text-fg-tertiary">로그아웃 상태</span>
        )}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1">
          <Label htmlFor="auth-demo-id">이메일 또는 아이디</Label>
          <Input
            id="auth-demo-id"
            value={loginId}
            onChange={(e) => setLoginId(e.target.value)}
            autoComplete="username"
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="auth-demo-pw">비밀번호</Label>
          <Input
            id="auth-demo-pw"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={onLogin} disabled={busy}>
          로그인
        </Button>
        <Button
          variant="secondary"
          onClick={() => {
            setPassword("wrong-password");
            void onLogin();
          }}
          disabled={busy}
        >
          잘못된 비번으로 시도
        </Button>
        <Button
          variant="outline"
          onClick={() => {
            void logout();
          }}
          disabled={busy}
        >
          로그아웃
        </Button>
      </div>

      <p className="text-xs text-fg-tertiary">
        MSW mock 자격증명: <span className="font-mono">admin_rown / Admin_rown12!</span>
      </p>
    </div>
  );
}
