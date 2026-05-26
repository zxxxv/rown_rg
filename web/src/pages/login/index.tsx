import { zodResolver } from "@hookform/resolvers/zod";
import { AlertCircle, LockKeyhole } from "lucide-react";
import type { KeyboardEvent } from "react";
import { Controller, useForm } from "react-hook-form";
import { useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "@/api/client";
import { type LoginInput, LoginInputSchema } from "@/api/types";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/hooks/useAuth";

interface LocationFromState {
  from?: { pathname?: string };
}

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const {
    control,
    handleSubmit,
    formState: { errors, isSubmitting },
    setError,
    clearErrors,
  } = useForm<LoginInput>({
    resolver: zodResolver(LoginInputSchema),
    defaultValues: { id: "", password: "" },
    mode: "onSubmit",
  });

  const submit = handleSubmit(async (values) => {
    try {
      await login(values);
      const from = (location.state as LocationFromState | null)?.from?.pathname;
      navigate(from && from !== "/login" ? from : "/projects", { replace: true });
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

  const onEnter = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && !isSubmitting) {
      e.preventDefault();
      void submit();
    }
  };

  const rootError = errors.root?.message;
  const isLocked = errors.root?.type === "locked";

  return (
    <main className="flex min-h-screen items-center justify-center bg-bg-secondary px-4 py-10">
      <Card className="w-full max-w-[400px]">
        <CardHeader className="space-y-3 text-center">
          <div
            aria-hidden
            className="mx-auto inline-flex h-12 w-12 items-center justify-center rounded-md bg-accent text-accent-foreground"
          >
            <LockKeyhole className="h-6 w-6" />
          </div>
          <CardTitle className="text-xl">AI 보고서 생성 시스템</CardTitle>
          <CardDescription>로운인사이트 사번 또는 이메일로 로그인하세요.</CardDescription>
        </CardHeader>

        <CardContent className="flex flex-col gap-4">
          {rootError ? (
            <Alert variant={isLocked ? "default" : "destructive"}>
              <AlertCircle className="h-4 w-4" />
              <AlertTitle>{isLocked ? "계정 잠김" : "로그인 실패"}</AlertTitle>
              <AlertDescription>{rootError}</AlertDescription>
            </Alert>
          ) : null}

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="login-id">사번 또는 이메일</Label>
            <Controller
              name="id"
              control={control}
              render={({ field }) => (
                <Input
                  id="login-id"
                  type="text"
                  autoComplete="username"
                  autoFocus
                  disabled={isSubmitting || isLocked}
                  aria-invalid={errors.id ? "true" : undefined}
                  aria-describedby={errors.id ? "login-id-error" : undefined}
                  onKeyDown={onEnter}
                  {...field}
                  onChange={(e) => {
                    if (rootError) clearErrors("root");
                    field.onChange(e);
                  }}
                />
              )}
            />
            {errors.id ? (
              <p id="login-id-error" className="text-xs text-fg-danger">
                {errors.id.message}
              </p>
            ) : null}
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="login-password">비밀번호</Label>
            <Controller
              name="password"
              control={control}
              render={({ field }) => (
                <Input
                  id="login-password"
                  type="password"
                  autoComplete="current-password"
                  disabled={isSubmitting || isLocked}
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
            className="w-full"
            onClick={() => void submit()}
            disabled={isSubmitting || isLocked}
          >
            {isSubmitting ? "로그인 중…" : "로그인"}
          </Button>

          <p className="text-center text-xs text-fg-tertiary">
            비밀번호는 90일마다 변경해야 합니다. 잊으셨다면 관리자에게 문의하세요.
          </p>
        </CardContent>
      </Card>
    </main>
  );
}
