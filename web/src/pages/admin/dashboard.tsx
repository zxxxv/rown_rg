import { Activity, DollarSign, FileCheck2, Users } from "lucide-react";
import { useMemo } from "react";
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
import { useAdminDashboard, useApproveQuotaExtension } from "@/api/admin";
import { ApiError } from "@/api/client";
import type { AdminDashboardData, QuotaRequest, UserUsageRow } from "@/api/mock/fixtures/admin";
import { StatCard } from "@/components/data-display/StatCard";
import { StatusDot, type StatusKind } from "@/components/data-display/StatusDot";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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

export default function AdminDashboardPage() {
  const { user, logout } = useAuth();
  const dashboard = useAdminDashboard();

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
                ? `이번 달 ${dashboard.data.period.label} 사용 현황`
                : "사용 현황 불러오는 중…"}
            </p>
          </div>
          <PeriodSelect />
        </header>

        {dashboard.isLoading ? (
          <LoadingSkeleton variant="card" count={4} />
        ) : dashboard.isError || !dashboard.data ? (
          <EmptyState
            title="대시보드를 불러오지 못했습니다"
            description="잠시 후 다시 시도해 주세요."
            action={
              <Button variant="outline" onClick={() => void dashboard.refetch()}>
                다시 시도
              </Button>
            }
          />
        ) : (
          <DashboardBody data={dashboard.data} />
        )}
      </div>
    </AppShell>
  );
}

function PeriodSelect() {
  return (
    <Select defaultValue="this_month">
      <SelectTrigger className="w-40">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value="this_month">이번 달</SelectItem>
        <SelectItem value="last_month" disabled>
          지난 달 (Phase 5)
        </SelectItem>
        <SelectItem value="last_7d" disabled>
          최근 7일 (Phase 5)
        </SelectItem>
        <SelectItem value="last_30d" disabled>
          최근 30일 (Phase 5)
        </SelectItem>
      </SelectContent>
    </Select>
  );
}

function DashboardBody({ data }: { data: AdminDashboardData }) {
  const costPct = (data.kpis.total_cost_usd / data.kpis.cost_limit_usd) * 100;
  const costTone = costPct >= 90 ? "danger" : costPct >= 70 ? "warning" : "default";

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="이번 달 총 비용"
          value={`$${data.kpis.total_cost_usd.toLocaleString()}`}
          hint={`한도 $${data.kpis.cost_limit_usd.toLocaleString()} 중 ${costPct.toFixed(0)}%`}
          icon={DollarSign}
          tone={costTone}
          progress={{ current: data.kpis.total_cost_usd, max: data.kpis.cost_limit_usd }}
        />
        <StatCard
          label="활성 사용자"
          value={`${data.kpis.active_users}명`}
          hint="이번 달 1회 이상 로그인"
          icon={Users}
        />
        <StatCard
          label="진행 중 프로젝트"
          value={`${data.kpis.active_projects}건`}
          hint="status = writing / qa / review"
          icon={Activity}
        />
        <StatCard
          label="완료된 보고서"
          value={`${data.kpis.completed_reports}건`}
          hint="이번 달 완료"
          icon={FileCheck2}
          tone="success"
        />
      </div>

      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-2 space-y-0">
          <div>
            <CardTitle className="text-base">일별 비용 추세 (최근 30일)</CardTitle>
            <CardDescription>USD 기준, 일별 총 사용 비용</CardDescription>
          </div>
          <span className="font-mono text-xs text-fg-tertiary">
            평균 ${Math.round(data.kpis.total_cost_usd / data.daily_costs.length)}/일
          </span>
        </CardHeader>
        <CardContent>
          <div className="h-80 w-full">
            <DailyCostChart points={data.daily_costs} />
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
        <UsageTable rows={data.user_usage} />
        <QuotaRequestsPanel requests={data.quota_requests} />
      </div>
    </div>
  );
}

const ACCENT = "var(--color-accent)";
const WARNING = "var(--color-text-warning)";

function DailyCostChart({ points }: { points: AdminDashboardData["daily_costs"] }) {
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
        <Area
          type="monotone"
          dataKey="users"
          stroke={WARNING}
          strokeWidth={1}
          fill="none"
          strokeDasharray="3 3"
        />
      </AreaChart>
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

function UsageTable({ rows }: { rows: UserUsageRow[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">사용자별 사용량</CardTitle>
        <CardDescription>이번 달 토큰·비용 누적</CardDescription>
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
                  className={cn(
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
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() =>
                        toast(`${row.name}의 한도 조정 (Phase 5에서 작동)`, {
                          description: `현재 한도: $${row.limit_usd}`,
                        })
                      }
                    >
                      한도 조정
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
