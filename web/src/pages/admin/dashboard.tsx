import { Activity, DollarSign, FileCheck2, FolderKanban, Users } from "lucide-react";
import { useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { toast } from "sonner";
import {
  type AdminDashboardParams,
  useAdminDashboard,
  useApproveQuotaExtension,
  useUserUsageDetail,
} from "@/api/admin";
import { ApiError } from "@/api/client";
import type {
  AdminDashboardData,
  AdminDashboardPeriod,
  QuotaRequest,
  UserDailySeries,
  UserUsageDetail,
  UserUsageRow,
} from "@/api/mock/fixtures/admin";
import { useSetUserQuota } from "@/api/users";
import { StatCard } from "@/components/data-display/StatCard";
import { StatusDot, type StatusKind } from "@/components/data-display/StatusDot";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";

const PERIOD_LABEL: Record<AdminDashboardPeriod, string> = {
  this_month: "이번 달",
  last_month: "지난 달",
  last_7_days: "최근 7일",
  last_30_days: "최근 30일",
  custom: "구간 선택",
};

export default function AdminDashboardPage() {
  const { user, logout } = useAuth();
  const [period, setPeriod] = useState<AdminDashboardPeriod>("last_30_days");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const [detailUserId, setDetailUserId] = useState<string | null>(null);
  const params = { period, start: customStart, end: customEnd };
  const dashboard = useAdminDashboard(params);

  return (
    <AppShell
      user={user ? { name: user.name, role: user.role } : null}
      onLogout={() => void logout()}
      tokenUsage={{ used: 1_240_000, limit: 5_000_000 }}
    >
      <div className="flex flex-col gap-6">
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-3xl font-semibold text-fg">관리자 대시보드</h1>
            <p className="text-sm text-fg-secondary">
              {dashboard.data
                ? `${PERIOD_LABEL[dashboard.data.period.type]} 사용 현황 (${dashboard.data.period.label})`
                : "사용 현황 불러오는 중…"}
            </p>
          </div>
          <PeriodSelect
            value={period}
            onChange={setPeriod}
            start={customStart}
            end={customEnd}
            onStart={setCustomStart}
            onEnd={setCustomEnd}
          />
        </header>

        {dashboard.isLoading ? (
          <LoadingSkeleton variant="card" count={4} />
        ) : dashboard.isError || !dashboard.data ? (
          <EmptyState
            title="대시보드를 불러오지 못했습니다"
            description={
              period === "custom" && !(customStart && customEnd)
                ? "구간(시작·종료 날짜)을 선택하세요."
                : "잠시 후 다시 시도해 주세요."
            }
            action={
              <Button variant="outline" onClick={() => void dashboard.refetch()}>
                다시 시도
              </Button>
            }
          />
        ) : (
          <DashboardBody data={dashboard.data} onSelectUser={setDetailUserId} />
        )}
      </div>

      <UserDetailDialog
        userId={detailUserId}
        params={params}
        onClose={() => setDetailUserId(null)}
      />
    </AppShell>
  );
}

function PeriodSelect({
  value,
  onChange,
  start,
  end,
  onStart,
  onEnd,
}: {
  value: AdminDashboardPeriod;
  onChange: (period: AdminDashboardPeriod) => void;
  start: string;
  end: string;
  onStart: (v: string) => void;
  onEnd: (v: string) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Select value={value} onValueChange={(v) => onChange(v as AdminDashboardPeriod)}>
        <SelectTrigger className="w-36">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="last_30_days">최근 30일</SelectItem>
          <SelectItem value="last_7_days">최근 7일</SelectItem>
          <SelectItem value="this_month">이번 달</SelectItem>
          <SelectItem value="last_month">지난 달</SelectItem>
          <SelectItem value="custom">구간 선택</SelectItem>
        </SelectContent>
      </Select>
      {value === "custom" ? (
        <div className="flex items-center gap-1">
          <Input
            type="date"
            value={start}
            max={end || undefined}
            onChange={(e) => onStart(e.target.value)}
            className="w-36"
            aria-label="시작 날짜"
          />
          <span className="text-fg-tertiary">~</span>
          <Input
            type="date"
            value={end}
            min={start || undefined}
            onChange={(e) => onEnd(e.target.value)}
            className="w-36"
            aria-label="종료 날짜"
          />
        </div>
      ) : null}
    </div>
  );
}

function DashboardBody({
  data,
  onSelectUser,
}: {
  data: AdminDashboardData;
  onSelectUser: (userId: string) => void;
}) {
  const costPct = (data.kpis.total_cost_usd / data.kpis.cost_limit_usd) * 100;
  const costTone = costPct >= 90 ? "danger" : costPct >= 70 ? "warning" : "default";
  const periodLabel = PERIOD_LABEL[data.period.type];

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label={`${periodLabel} 총 비용`}
          value={`$${data.kpis.total_cost_usd.toLocaleString()}`}
          hint={`한도 $${data.kpis.cost_limit_usd.toLocaleString()} 중 ${costPct.toFixed(0)}%`}
          icon={DollarSign}
          tone={costTone}
          progress={{ current: data.kpis.total_cost_usd, max: data.kpis.cost_limit_usd }}
        />
        <StatCard
          label="활성 사용자"
          value={`${data.kpis.active_users}명`}
          hint={`${periodLabel} 1회 이상 로그인`}
          icon={Users}
        />
        <StatCard
          label="진행 중 프로젝트"
          value={`${data.kpis.active_projects}건`}
          hint="status = researching / indexing / writing / reviewing"
          icon={Activity}
        />
        <StatCard
          label="완료된 보고서"
          value={`${data.kpis.completed_reports}건`}
          hint={`${periodLabel} 완료`}
          icon={FileCheck2}
          tone="success"
        />
      </div>

      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-2 space-y-0">
          <div>
            <CardTitle className="text-base">일별 비용 추세 ({periodLabel})</CardTitle>
            <CardDescription>USD 기준, 일별 총 사용 비용</CardDescription>
          </div>
          <span className="font-mono text-xs text-fg-tertiary">
            평균 ${Math.round(data.kpis.total_cost_usd / data.daily_costs.length)}/일
          </span>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          <div className="flex items-center gap-4 text-xs text-fg-tertiary">
            <span className="flex items-center gap-1.5">
              <span
                aria-hidden
                className="inline-block w-5 shrink-0 border-t-2 border-solid"
                style={{ borderColor: "var(--color-accent)" }}
              />
              비용 (USD, 실선)
            </span>
            <span className="flex items-center gap-1.5">
              <span
                aria-hidden
                className="inline-block w-5 shrink-0 border-t-2 border-dashed"
                style={{ borderColor: "var(--color-text-warning)" }}
              />
              활성 사용자 수 (점선)
            </span>
          </div>
          <div className="h-80 w-full">
            <DailyCostChart points={data.daily_costs} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">날짜별 사용자 기여 ({periodLabel})</CardTitle>
          <CardDescription>상위 6명 + 기타를 쌓아 '누가 언제 얼마나' 썼는지 표시</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="h-80 w-full">
            <UserDailyChart series={data.user_daily} onSelectUser={onSelectUser} />
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
        <UsageTable rows={data.user_usage} periodLabel={periodLabel} onSelectUser={onSelectUser} />
        <QuotaRequestsPanel requests={data.quota_requests} />
      </div>
    </div>
  );
}

const ACCENT = "var(--color-accent)";
const WARNING = "var(--color-text-warning)";

function DailyCostChart({
  points,
  showUsers = true,
}: {
  points: AdminDashboardData["daily_costs"];
  // 단일 사용자 상세에선 '사용자 명수'가 무의미하므로 끈다(금액만).
  showUsers?: boolean;
}) {
  const data = useMemo(
    () =>
      points.map((p) => ({
        date: p.date.slice(5),
        cost: p.cost_usd,
        users: p.users,
      })),
    [points],
  );

  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="costGradient" x1="0" y1="0" x2="0" y2="1">
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
          interval={4}
          stroke="var(--color-border-primary)"
        />
        <YAxis
          tick={{ fontSize: 11, fill: "var(--color-text-tertiary)" }}
          stroke="var(--color-border-primary)"
          tickFormatter={(v) => `$${v}`}
          width={48}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: "var(--color-background-primary)",
            border: "1px solid var(--color-border-primary)",
            borderRadius: 6,
            fontSize: 12,
          }}
          formatter={(value, name) => {
            if (name === "cost") return [`$${value}`, "비용"];
            return [`${value}명`, "사용자"];
          }}
        />
        <Area
          type="monotone"
          dataKey="cost"
          stroke={ACCENT}
          strokeWidth={2}
          fill="url(#costGradient)"
        />
        {showUsers ? (
          <Area
            type="monotone"
            dataKey="users"
            stroke={WARNING}
            strokeWidth={1}
            fill="none"
            strokeDasharray="3 3"
          />
        ) : null}
      </AreaChart>
    </ResponsiveContainer>
  );
}

// 카테고리형 팔레트(dataviz 검증 슬롯 1–6, 스택 인접쌍 통과). '기타'는 중립 회색.
const USER_SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"];
const OTHER_COLOR = "var(--color-text-tertiary)";

function UserDailyChart({
  series,
  onSelectUser,
}: {
  // 백엔드 버전 스큐(옛 응답엔 user_daily 없음)에도 안 터지게 undefined 허용.
  series: UserDailySeries | undefined;
  onSelectUser: (userId: string) => void;
}) {
  const users = series?.users ?? [];
  const points = series?.points ?? [];
  const nameByKey = useMemo(() => new Map(users.map((u) => [u.key, u.name])), [users]);
  const data = useMemo(
    () =>
      points.map((p) => {
        const row: Record<string, number | string> = { date: p.date.slice(5) };
        for (const u of users) row[u.key] = p.costs[u.key] ?? 0;
        return row;
      }),
    [points, users],
  );
  const colorFor = (key: string, idx: number) =>
    key === "other" ? OTHER_COLOR : (USER_SERIES_COLORS[idx % USER_SERIES_COLORS.length] as string);

  if (users.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-fg-tertiary">
        이 기간에 사용 기록이 없습니다.
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid
          strokeDasharray="3 3"
          stroke="var(--color-border-tertiary)"
          vertical={false}
        />
        <XAxis
          dataKey="date"
          tick={{ fontSize: 11, fill: "var(--color-text-tertiary)" }}
          interval={Math.max(0, Math.floor(data.length / 8))}
          stroke="var(--color-border-primary)"
        />
        <YAxis
          tick={{ fontSize: 11, fill: "var(--color-text-tertiary)" }}
          stroke="var(--color-border-primary)"
          tickFormatter={(v) => `$${v}`}
          width={48}
        />
        <Tooltip
          contentStyle={{
            backgroundColor: "var(--color-background-primary)",
            border: "1px solid var(--color-border-primary)",
            borderRadius: 6,
            fontSize: 12,
          }}
          formatter={(value, key) => [`$${value}`, nameByKey.get(String(key)) ?? String(key)]}
        />
        <Legend
          formatter={(key) => nameByKey.get(String(key)) ?? String(key)}
          wrapperStyle={{ fontSize: 11 }}
        />
        {users.map((u, idx) => (
          <Bar
            key={u.key}
            dataKey={u.key}
            stackId="cost"
            fill={colorFor(u.key, idx)}
            // 스택 세그먼트 사이 표면 갭(가독성) - dataviz 규약.
            stroke="var(--color-background-primary)"
            strokeWidth={1}
            cursor={u.key === "other" ? undefined : "pointer"}
            onClick={() => {
              if (u.key !== "other") onSelectUser(u.key);
            }}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

function usagePct(row: UserUsageRow): number {
  return Math.round((row.cost_usd / Math.max(1, row.limit_usd)) * 100);
}

function usageTone(pct: number): "default" | "warning" | "danger" {
  if (pct >= 100) return "danger";
  if (pct >= 80) return "warning";
  return "default";
}

const ROLE_KIND: Record<string, StatusKind> = {
  admin: "info",
  super_admin: "info",
  worker: "tertiary",
  viewer: "tertiary",
};

const ROLE_LABEL: Record<string, string> = {
  super_admin: "최고관리자",
  admin: "관리자",
  worker: "작성자",
  viewer: "뷰어",
};

function UsageTable({
  rows,
  periodLabel,
  onSelectUser,
}: {
  rows: UserUsageRow[];
  periodLabel: string;
  onSelectUser: (userId: string) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">사용자별 사용량</CardTitle>
        <CardDescription>{periodLabel} 토큰·비용 누적 - 행을 클릭하면 상세</CardDescription>
      </CardHeader>
      <CardContent className="p-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>사용자</TableHead>
              <TableHead className="text-right">토큰</TableHead>
              <TableHead className="text-right">비용</TableHead>
              <TableHead className="text-right">한도</TableHead>
              <TableHead>한도 대비</TableHead>
              <TableHead>최근 활동</TableHead>
              <TableHead className="text-right">액션</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => {
              const pct = usagePct(row);
              const tone = usageTone(pct);
              return (
                <TableRow
                  key={row.user_id}
                  onClick={() => onSelectUser(row.user_id)}
                  className={cn(
                    "cursor-pointer",
                    tone === "danger" && "bg-bg-danger/30",
                    tone === "warning" && "bg-bg-warning/20",
                  )}
                >
                  <TableCell>
                    <div className="flex flex-col gap-0.5">
                      <span className="text-sm font-medium text-fg">{row.name}</span>
                      <div className="flex items-center gap-2 font-mono text-[11px] text-fg-tertiary">
                        <span>{row.email}</span>
                        <StatusDot
                          kind={ROLE_KIND[row.role] ?? "tertiary"}
                          label={ROLE_LABEL[row.role] ?? row.role}
                        />
                      </div>
                    </div>
                  </TableCell>
                  <TableCell className="text-right font-mono text-sm">
                    {(row.tokens_used / 1_000_000).toFixed(1)}M
                  </TableCell>
                  <TableCell className="text-right font-mono text-sm">${row.cost_usd}</TableCell>
                  <TableCell className="text-right font-mono text-sm text-fg-tertiary">
                    ${row.limit_usd}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <div className="h-1.5 w-20 overflow-hidden rounded-full bg-bg-tertiary">
                        <div
                          className={cn(
                            "h-full transition-[width]",
                            tone === "danger"
                              ? "bg-fg-danger"
                              : tone === "warning"
                                ? "bg-fg-warning"
                                : "bg-accent",
                          )}
                          style={{ width: `${Math.min(100, pct)}%` }}
                        />
                      </div>
                      <span
                        className={cn(
                          "font-mono text-xs",
                          tone === "danger" && "text-fg-danger",
                          tone === "warning" && "text-fg-warning",
                          tone === "default" && "text-fg-tertiary",
                        )}
                      >
                        {pct}%
                      </span>
                    </div>
                  </TableCell>
                  <TableCell className="font-mono text-xs text-fg-tertiary">
                    {row.last_active.slice(0, 10)}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button variant="ghost" size="sm" onClick={() => onSelectUser(row.user_id)}>
                      상세
                    </Button>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function MiniStat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="flex flex-col gap-0.5 rounded border border-border bg-bg-secondary px-3 py-2">
      <span className="text-[11px] text-fg-tertiary">{label}</span>
      <span className="font-mono text-base font-semibold text-fg">{value}</span>
      {sub ? <span className="font-mono text-[11px] text-fg-tertiary">{sub}</span> : null}
    </div>
  );
}

/** 사용자 클릭 상세 - 기간 지출·보고서 건수·프로젝트별 비용·일별 추이 + 한도 조정(인라인 흡수). */
function UserDetailDialog({
  userId,
  params,
  onClose,
}: {
  userId: string | null;
  params: AdminDashboardParams;
  onClose: () => void;
}) {
  const detail = useUserUsageDetail(userId, params);
  return (
    <Dialog open={userId !== null} onOpenChange={(open) => (!open ? onClose() : undefined)}>
      <DialogContent className="max-w-2xl">
        {detail.isLoading || !detail.data ? (
          <div className="py-10">
            <LoadingSkeleton variant="row" count={5} />
          </div>
        ) : (
          <UserDetailBody data={detail.data} onDone={onClose} />
        )}
      </DialogContent>
    </Dialog>
  );
}

function UserDetailBody({ data, onDone }: { data: UserUsageDetail; onDone: () => void }) {
  const setQuota = useSetUserQuota();
  const [amount, setAmount] = useState("");
  const parsed = Number(amount);
  const valid = amount.trim() !== "" && Number.isFinite(parsed) && parsed >= 0;
  const pct = Math.round((data.total_cost_usd / Math.max(1, data.limit_usd)) * 100);

  const saveQuota = () => {
    if (!valid) return;
    setQuota.mutate(
      { userId: data.user_id, monthly_limit_usd: parsed },
      {
        onSuccess: (res) => {
          toast.success(`${data.name}의 월 한도를 $${res.monthly_limit_usd}로 설정했습니다.`);
          setAmount("");
        },
        onError: (err) => {
          const msg = err instanceof ApiError ? err.message : "처리 중 오류";
          toast.error("한도 조정 실패", { description: msg });
        },
      },
    );
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2">
          {data.name}
          <StatusDot
            kind={ROLE_KIND[data.role] ?? "tertiary"}
            label={ROLE_LABEL[data.role] ?? data.role}
          />
        </DialogTitle>
        <DialogDescription>
          {data.email} · {data.period.label}
        </DialogDescription>
      </DialogHeader>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <MiniStat label="기간 지출" value={`$${data.total_cost_usd}`} />
        <MiniStat label="월 한도" value={`$${data.limit_usd}`} sub={`${pct}%`} />
        <MiniStat label="완료 보고서" value={`${data.reports_completed}건`} />
        <MiniStat label="진행 중" value={`${data.reports_in_progress}건`} />
      </div>

      <div className="h-40 w-full">
        <DailyCostChart points={data.daily_costs} showUsers={false} />
      </div>

      <div className="max-h-56 overflow-y-auto rounded border border-border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>프로젝트 (총 {data.reports_total})</TableHead>
              <TableHead>상태</TableHead>
              <TableHead className="text-right">비용</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.projects.length === 0 ? (
              <TableRow>
                <TableCell colSpan={3} className="text-center text-xs text-fg-tertiary">
                  이 기간에 프로젝트가 없습니다.
                </TableCell>
              </TableRow>
            ) : (
              data.projects.map((p) => (
                <TableRow key={p.id}>
                  <TableCell className="max-w-[280px]">
                    <span className="flex items-center gap-2">
                      <FolderKanban className="h-3.5 w-3.5 shrink-0 text-fg-tertiary" aria-hidden />
                      <span className="truncate text-sm text-fg">{p.title}</span>
                    </span>
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-fg-secondary">
                    {p.status}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-right font-mono text-sm">
                    ${p.cost_usd}
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <div className="flex items-end gap-2 border-t border-border pt-3">
        <div className="flex flex-1 flex-col gap-1.5">
          <Label htmlFor="detail-quota">월 한도 조정 (USD)</Label>
          <Input
            id="detail-quota"
            type="number"
            min={0}
            step={10}
            inputMode="decimal"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
            placeholder={String(data.limit_usd)}
          />
        </div>
        <Button onClick={saveQuota} disabled={!valid || setQuota.isPending}>
          {setQuota.isPending ? "저장 중…" : "한도 저장"}
        </Button>
      </div>

      <DialogFooter>
        <Button variant="ghost" onClick={onDone}>
          닫기
        </Button>
      </DialogFooter>
    </>
  );
}

function QuotaRequestsPanel({ requests }: { requests: QuotaRequest[] }) {
  const approve = useApproveQuotaExtension();
  const pending = requests.filter((r) => r.status === "pending");

  const decide = (req: QuotaRequest, decision: "approved" | "rejected") => {
    approve.mutate(
      { requestId: req.id, decision },
      {
        onSuccess: () => {
          toast.success(
            decision === "approved"
              ? `${req.user_name}의 $${req.amount_usd} 추가 요청 승인`
              : `${req.user_name}의 요청 거부`,
          );
        },
        onError: (err) => {
          const msg = err instanceof ApiError ? err.message : "처리 실패";
          toast.error("처리 실패", { description: msg });
        },
      },
    );
  };

  return (
    <Card className="xl:sticky xl:top-6 xl:self-start">
      <CardHeader>
        <CardTitle className="text-base">한도 초과 승인 요청</CardTitle>
        <CardDescription>
          {pending.length > 0 ? `대기 중 ${pending.length}건` : "대기 중인 요청 없음"}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {pending.length === 0 ? (
          <p className="rounded border border-dashed border-border bg-bg-secondary p-3 text-xs text-fg-tertiary">
            처리 대기 중인 요청이 없습니다.
          </p>
        ) : (
          pending.map((req) => (
            <div
              key={req.id}
              className="flex flex-col gap-2 rounded border border-fg-warning/30 bg-bg-warning/40 p-3"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium text-fg">{req.user_name}</span>
                <Badge variant="secondary" className="font-mono">
                  +${req.amount_usd}
                </Badge>
              </div>
              <p className="text-xs text-fg-secondary">{req.reason}</p>
              <p className="font-mono text-[11px] text-fg-tertiary">
                {req.requested_at.slice(0, 16).replace("T", " ")}
              </p>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  className="flex-1"
                  disabled={approve.isPending}
                  onClick={() => decide(req, "approved")}
                >
                  승인
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="flex-1"
                  disabled={approve.isPending}
                  onClick={() => decide(req, "rejected")}
                >
                  거부
                </Button>
              </div>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}
