import { zodResolver } from "@hookform/resolvers/zod";
import {
  ArrowUpRight,
  Calendar,
  Clock,
  Coins,
  KeyRound,
  Mail,
  ShieldCheck,
  ShieldHalf,
} from "lucide-react";
import { useMemo } from "react";
import { Controller, useForm } from "react-hook-form";
import { Link } from "react-router-dom";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { useChangePassword, useMyTokenUsage } from "@/api/profile";
import {
  type ChangePasswordInput,
  ChangePasswordInputSchema,
  type MyTokenUsage,
  type User,
} from "@/api/types";
import { hasRoleAtLeast } from "@/components/auth/RequireAuth";
import { StatCard } from "@/components/data-display/StatCard";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAuth } from "@/hooks/useAuth";

const ROLE_LABEL: Record<string, string> = {
  super_admin: "최고관리자",
  admin: "관리자",
  worker: "작성자",
  viewer: "뷰어",
};

const PASSWORD_ROTATION_DAYS = 90;

function fmtDateTime(iso?: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function fmtDate(iso?: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("ko-KR", {
    timeZone: "Asia/Seoul",
    dateStyle: "medium",
  });
}

function daysSince(iso?: string | null): number | null {
  if (!iso) return null;
  return Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

export default function ProfilePage() {
  const { user, logout } = useAuth();
  const usage = useMyTokenUsage();

  if (!user) {
    return (
      <AppShell user={null}>
        <LoadingSkeleton variant="block" />
      </AppShell>
    );
  }

  return (
    <AppShell user={{ name: user.name, role: user.role }} onLogout={() => void logout()}>
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-6">
        <header>
          <h1 className="text-3xl font-semibold text-fg">마이페이지</h1>
          <p className="text-sm text-fg-secondary">내 계정 정보와 이번 달 사용량을 확인하세요.</p>
        </header>

        <ProfileCard user={user} />

        <section className="flex flex-col gap-3">
          <h2 className="text-lg font-semibold text-fg">이번 달 사용량</h2>
          {usage.isLoading ? (
            <LoadingSkeleton variant="card" count={4} />
          ) : usage.isError || !usage.data ? (
            <EmptyState
              title="사용량을 불러오지 못했습니다"
              description="잠시 후 다시 시도해 주세요."
              action={
                <Button variant="outline" onClick={() => void usage.refetch()}>
                  다시 시도
                </Button>
              }
            />
          ) : (
            <UsageSection data={usage.data} />
          )}
        </section>

        <section className="flex flex-col gap-3">
          <h2 className="text-lg font-semibold text-fg">보안</h2>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {user.has_password ? <ChangePasswordCard user={user} /> : <SsoAccountCard />}
            <TwoFactorCard />
          </div>
        </section>

        <SessionCard onLogout={() => void logout()} />
      </div>
    </AppShell>
  );
}

function ProfileCard({ user }: { user: User }) {
  const isAdmin = hasRoleAtLeast(user.role, "admin");
  return (
    <Card>
      <CardContent className="flex flex-col gap-5 pt-6">
        <div className="flex flex-wrap items-center gap-4">
          <span
            aria-hidden
            className="inline-flex h-14 w-14 items-center justify-center rounded-full bg-accent font-mono text-xl text-accent-foreground"
          >
            {user.name.slice(0, 1)}
          </span>
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <span className="text-xl font-semibold text-fg">{user.name}</span>
              <Badge variant="secondary">{ROLE_LABEL[user.role] ?? user.role}</Badge>
              {user.is_active === false ? <Badge variant="destructive">비활성</Badge> : null}
            </div>
            <span className="flex items-center gap-1.5 font-mono text-sm text-fg-secondary">
              <Mail className="h-3.5 w-3.5" aria-hidden />
              {user.email}
            </span>
          </div>
          {isAdmin ? (
            <Button asChild variant="outline" size="sm" className="ml-auto">
              <Link to="/admin/dashboard">
                관리자 콘솔
                <ArrowUpRight className="ml-1 h-4 w-4" />
              </Link>
            </Button>
          ) : null}
        </div>

        <Separator />

        <dl className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <InfoItem icon={ShieldHalf} label="권한" value={ROLE_LABEL[user.role] ?? user.role} />
          <InfoItem icon={Calendar} label="가입일" value={fmtDate(user.created_at)} />
          <InfoItem icon={Clock} label="최근 로그인" value={fmtDateTime(user.last_login_at)} />
        </dl>
      </CardContent>
    </Card>
  );
}

function InfoItem({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Mail;
  label: string;
  value: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="flex items-center gap-1.5 text-xs text-fg-tertiary">
        <Icon className="h-3.5 w-3.5" aria-hidden />
        {label}
      </dt>
      <dd className="text-sm text-fg">{value}</dd>
    </div>
  );
}

const ACCENT = "var(--color-accent)";

function UsageSection({ data }: { data: MyTokenUsage }) {
  const totalTokens = data.total_input_tokens + data.total_output_tokens;
  const chartData = useMemo(
    () => data.daily.map((d) => ({ date: d.date.slice(5), cost: d.cost_usd })),
    [data.daily],
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="이번 달 비용" value={`$${data.total_cost_usd.toFixed(2)}`} icon={Coins} />
        <StatCard
          label="총 토큰"
          value={fmtTokens(totalTokens)}
          hint={`요청 ${data.request_count}건`}
        />
        <StatCard label="입력 토큰" value={fmtTokens(data.total_input_tokens)} />
        <StatCard label="출력 토큰" value={fmtTokens(data.total_output_tokens)} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">일별 비용</CardTitle>
          <CardDescription>
            {data.period_start} ~ {data.period_end} · USD 기준
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="profileCostGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={ACCENT} stopOpacity={0.35} />
                    <stop offset="95%" stopColor={ACCENT} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke="var(--color-border-tertiary)"
                  vertical={false}
                />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 11, fill: "var(--color-text-tertiary)" }}
                  interval={3}
                  stroke="var(--color-border-primary)"
                />
                <YAxis
                  tick={{ fontSize: 11, fill: "var(--color-text-tertiary)" }}
                  stroke="var(--color-border-primary)"
                  tickFormatter={(v) => `$${v}`}
                  width={44}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "var(--color-background-primary)",
                    border: "1px solid var(--color-border-primary)",
                    borderRadius: 6,
                    fontSize: 12,
                  }}
                  formatter={(value) => [`$${value}`, "비용"]}
                />
                <Area
                  type="monotone"
                  dataKey="cost"
                  stroke={ACCENT}
                  strokeWidth={2}
                  fill="url(#profileCostGradient)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">모델별 사용량</CardTitle>
          <CardDescription>이번 달 누적</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>모델</TableHead>
                <TableHead className="text-right">요청</TableHead>
                <TableHead className="text-right">입력</TableHead>
                <TableHead className="text-right">출력</TableHead>
                <TableHead className="text-right">비용</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.by_model.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5} className="py-8 text-center text-sm text-fg-tertiary">
                    이번 달 사용 내역이 없습니다.
                  </TableCell>
                </TableRow>
              ) : (
                data.by_model.map((m) => (
                  <TableRow key={m.model}>
                    <TableCell className="font-mono text-sm text-fg">{m.model}</TableCell>
                    <TableCell className="text-right font-mono text-sm">
                      {m.request_count.toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right font-mono text-sm text-fg-secondary">
                      {fmtTokens(m.input_tokens)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-sm text-fg-secondary">
                      {fmtTokens(m.output_tokens)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-sm">
                      ${m.cost_usd.toFixed(2)}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}

function ChangePasswordCard({ user }: { user: User }) {
  const changePassword = useChangePassword();
  const {
    control,
    handleSubmit,
    reset,
    formState: { errors, isSubmitting },
    setError,
  } = useForm<ChangePasswordInput>({
    resolver: zodResolver(ChangePasswordInputSchema),
    defaultValues: { current_password: "", new_password: "", confirm_password: "" },
    mode: "onSubmit",
  });

  const sinceChange = daysSince(user.password_changed_at);
  const overdue = sinceChange !== null && sinceChange >= PASSWORD_ROTATION_DAYS;

  const submit = handleSubmit(async (values) => {
    try {
      await changePassword.mutateAsync(values);
      toast.success("비밀번호가 변경되었습니다.");
      reset();
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : "비밀번호 변경 중 오류가 발생했습니다.";
      // 현재 비밀번호 오류는 해당 필드에, 그 외에는 새 비밀번호 필드에 표시
      const field =
        err instanceof ApiError && err.code === "invalid_credentials"
          ? "current_password"
          : "new_password";
      setError(field, { type: "server", message });
    }
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <KeyRound className="h-4 w-4" aria-hidden />
          비밀번호 변경
        </CardTitle>
        <CardDescription>
          {sinceChange === null
            ? "비밀번호는 90일마다 변경하는 것을 권장합니다."
            : overdue
              ? `마지막 변경 후 ${sinceChange}일 지났습니다. 변경을 권장합니다.`
              : `마지막 변경: ${fmtDate(user.password_changed_at)} (${sinceChange}일 전)`}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <PasswordField
          control={control}
          name="current_password"
          label="현재 비밀번호"
          autoComplete="current-password"
          error={errors.current_password?.message}
        />
        <PasswordField
          control={control}
          name="new_password"
          label="새 비밀번호"
          autoComplete="new-password"
          error={errors.new_password?.message}
        />
        <PasswordField
          control={control}
          name="confirm_password"
          label="새 비밀번호 확인"
          autoComplete="new-password"
          error={errors.confirm_password?.message}
        />
        <Button
          type="button"
          className="w-fit"
          onClick={() => void submit()}
          disabled={isSubmitting}
        >
          {isSubmitting ? "변경 중…" : "비밀번호 변경"}
        </Button>
      </CardContent>
    </Card>
  );
}

function PasswordField({
  control,
  name,
  label,
  autoComplete,
  error,
}: {
  control: ReturnType<typeof useForm<ChangePasswordInput>>["control"];
  name: keyof ChangePasswordInput;
  label: string;
  autoComplete: string;
  error?: string;
}) {
  const id = `pw-${name}`;
  return (
    <div className="flex flex-col gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Controller
        name={name}
        control={control}
        render={({ field }) => (
          <Input
            id={id}
            type="password"
            autoComplete={autoComplete}
            aria-invalid={error ? "true" : undefined}
            {...field}
          />
        )}
      />
      {error ? <p className="text-xs text-fg-danger">{error}</p> : null}
    </div>
  );
}

function SsoAccountCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <ShieldCheck className="h-4 w-4" aria-hidden />
          로그인 방식
        </CardTitle>
        <CardDescription>네이버웍스(SSO) 전용 계정</CardDescription>
      </CardHeader>
      <CardContent>
        <p className="rounded border border-dashed border-border bg-bg-secondary p-3 text-sm text-fg-secondary">
          이 계정은 네이버웍스로 로그인합니다. 별도의 비밀번호가 없으며, 비밀번호 변경은 필요하지
          않습니다.
        </p>
      </CardContent>
    </Card>
  );
}

function TwoFactorCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <ShieldHalf className="h-4 w-4" aria-hidden />
          2단계 인증 (2FA)
          <Badge variant="outline" className="ml-1 text-[10px]">
            준비 중
          </Badge>
        </CardTitle>
        <CardDescription>TOTP 기반 2단계 인증</CardDescription>
      </CardHeader>
      <CardContent>
        <p className="rounded border border-dashed border-border bg-bg-secondary p-3 text-sm text-fg-tertiary">
          2단계 인증 등록 기능은 준비 중입니다. 활성화되면 인증 앱(QR)으로 등록할 수 있습니다.
        </p>
      </CardContent>
    </Card>
  );
}

function SessionCard({ onLogout }: { onLogout: () => void }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">세션</CardTitle>
        <CardDescription>로그아웃하면 모든 기기의 세션이 함께 종료됩니다.</CardDescription>
      </CardHeader>
      <CardContent>
        <Button variant="outline" onClick={onLogout}>
          모든 기기에서 로그아웃
        </Button>
      </CardContent>
    </Card>
  );
}
