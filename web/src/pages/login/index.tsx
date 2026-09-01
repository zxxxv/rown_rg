import { zodResolver } from "@hookform/resolvers/zod";
import { AlertCircle } from "lucide-react";
import { type KeyboardEvent, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { useLocation, useNavigate } from "react-router-dom";
import { useSsoStatus } from "@/api/auth";
import { ApiError } from "@/api/client";
import { type LoginInput, LoginInputSchema } from "@/api/types";
import { BrandMark } from "@/components/BrandMark";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PasswordInput } from "@/components/ui/password-input";
import { useAuth } from "@/hooks/useAuth";

interface LocationFromState {
  from?: { pathname?: string; search?: string; hash?: string };
}

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [ssoLoading, setSsoLoading] = useState(false);
  const { data: sso } = useSsoStatus();
  // 상태를 모르는 동안(로딩)엔 숨긴다 - 잠깐 보였다 사라지는 깜빡임이 더 어색하다.
  const ssoEnabled = sso?.enabled ?? false;

  const {
    control,
    handleSubmit,
    formState: { errors, isSubmitting },
    setError,
    clearErrors,
  } = useForm<LoginInput>({
    resolver: zodResolver(LoginInputSchema),
    defaultValues: { login_id: "", password: "" },
    mode: "onSubmit",
  });

  const goAfterLogin = () => {
    const fromLocation = (location.state as LocationFromState | null)?.from;
    const fromPath = fromLocation?.pathname;
    if (fromPath && fromPath !== "/login") {
      const search = fromLocation?.search ?? "";
      const hash = fromLocation?.hash ?? "";
      navigate(`${fromPath}${search}${hash}`, { replace: true });
    } else {
      navigate("/projects", { replace: true });
    }
  };

  const submit = handleSubmit(async (values) => {
    try {
      await login(values);
      goAfterLogin();
    } catch (err) {
      const status = err instanceof ApiError ? err.status : undefined;
      const message =
        err instanceof ApiError ? err.message : "로그인 중 알 수 없는 오류가 발생했습니다.";
      const locked = status === 423;
      setError("root", {
        type: locked ? "locked" : "auth",
        message: locked
          ? "5회 이상 로그인에 실패하여 계정이 잠겼습니다. 관리자에게 문의하세요."
          : message,
      });
    }
  });

  const onNaverWorks = () => {
    if (ssoLoading) return;
    clearErrors("root");
    setSsoLoading(true);
    // SAML SSO는 브라우저 전체 이동이어야 한다(XHR 불가).
    // 백엔드가 네이버웍스로 리다이렉트하고, 로그인 후 /callback 으로 돌아온다.
    window.location.href = "/api/v1/auth/saml/login";
  };

  const onEnter = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !isSubmitting) {
      e.preventDefault();
      void submit();
    }
  };

  const rootError = errors.root?.message;
  const isLocked = errors.root?.type === "locked";
  const busy = isSubmitting || ssoLoading;

  return (
    <main className="flex min-h-screen items-center justify-center bg-bg-secondary px-4 py-10">
      <Card className="w-full max-w-[400px]">
        <CardHeader className="space-y-3 text-center">
          <BrandMark className="mx-auto h-12 w-12" aria-hidden />
          <CardTitle className="text-xl">로운 리포트</CardTitle>
          <CardDescription>로운인사이트 계정으로 로그인하세요.</CardDescription>
        </CardHeader>

        <CardContent className="flex flex-col gap-4">
          {rootError ? (
            <Alert variant={isLocked ? "default" : "destructive"}>
              <AlertCircle className="h-4 w-4" />
              <AlertTitle>{isLocked ? "계정 잠김" : "로그인 실패"}</AlertTitle>
              <AlertDescription>{rootError}</AlertDescription>
            </Alert>
          ) : null}

          {/* SSO가 꺼져 있거나 IdP 값이 비면 버튼을 숨긴다 - 눌러도 실패하는
              버튼을 보여주는 게 더 나쁘다(상태는 인증 없이 조회). */}
          {ssoEnabled ? (
            <Button
              type="button"
              className="w-full bg-[#00C300] text-white hover:bg-[#00a800]"
              onClick={() => void onNaverWorks()}
              disabled={busy}
            >
              <span
                aria-hidden
                className="mr-2 inline-flex h-5 w-5 items-center justify-center rounded-sm bg-white font-bold text-[#00C300] text-xs"
              >
                W
              </span>
              {ssoLoading ? "네이버웍스 연결 중…" : "네이버웍스로 로그인"}
            </Button>
          ) : null}

          {ssoEnabled ? (
            <div className="flex items-center gap-3">
              <span className="h-px flex-1 bg-border" />
              <span className="text-xs text-fg-tertiary">또는 계정으로 로그인</span>
              <span className="h-px flex-1 bg-border" />
            </div>
          ) : null}

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="login-id">이메일 또는 아이디</Label>
            <Controller
              name="login_id"
              control={control}
              render={({ field }) => (
                <Input
                  id="login-id"
                  type="text"
                  autoComplete="username"
                  placeholder="name@naver.com 또는 아이디"
                  disabled={busy || isLocked}
                  aria-invalid={errors.login_id ? "true" : undefined}
                  aria-describedby={errors.login_id ? "login-id-error" : undefined}
                  onKeyDown={onEnter}
                  {...field}
                  onChange={(e) => {
                    if (rootError) clearErrors("root");
                    field.onChange(e);
                  }}
                />
              )}
            />
            {errors.login_id ? (
              <p id="login-id-error" className="text-xs text-fg-danger">
                {errors.login_id.message}
              </p>
            ) : null}
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="login-password">비밀번호</Label>
            <Controller
              name="password"
              control={control}
              render={({ field }) => (
                <PasswordInput
                  id="login-password"
                  autoComplete="current-password"
                  disabled={busy || isLocked}
                  aria-invalid={errors.password ? "true" : undefined}
                  aria-describedby={errors.password ? "login-password-error" : undefined}
                  onKeyDown={onEnter}
                  {...field}
                  onChange={(e) => {
                    if (rootError) clearErrors("root");
                    field.onChange(e);
                  }}
                />
              )}
            />
            {errors.password ? (
              <p id="login-password-error" className="text-xs text-fg-danger">
                {errors.password.message}
              </p>
            ) : null}
          </div>

          <Button
            type="button"
            variant="secondary"
            className="w-full"
            onClick={() => void submit()}
            disabled={busy || isLocked}
          >
            {isSubmitting ? "로그인 중…" : "로그인"}
          </Button>
        </CardContent>
      </Card>
    </main>
  );
}
