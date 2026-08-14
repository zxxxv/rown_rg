import type { UserRoleType } from "@/api/types";

export interface AdminKPI {
  total_cost_usd: number;
  cost_limit_usd: number;
  // 현재 접속중 - last_seen_at이 최근 5분 이내인 사용자 수 (기간 무관)
  online_users: number;
  // 기간 활성 - 조회 기간 내 1회 이상 로그인한 사용자 수
  active_users: number;
  active_projects: number;
  completed_reports: number;
}

export interface DailyCostPoint {
  date: string;
  cost_usd: number;
  users: number;
}

export interface UserUsageRow {
  user_id: string;
  name: string;
  email: string;
  role: UserRoleType;
  tokens_used: number;
  cost_usd: number;
  limit_usd: number;
  last_active: string;
}

export interface QuotaRequest {
  id: string;
  user_id: string;
  user_name: string;
  amount_usd: number;
  reason: string;
  requested_at: string;
  status: "pending" | "approved" | "rejected";
}

export type AdminDashboardPeriod =
  | "this_month"
  | "last_month"
  | "last_7_days"
  | "last_30_days"
  | "custom";

export interface UserSeriesMeta {
  key: string; // user_id 또는 "other"
  name: string; // 표시 이름 또는 "기타"
}

export interface UserDailyPoint {
  date: string;
  costs: Record<string, number>; // key → 그날 비용
}

export interface UserDailySeries {
  users: UserSeriesMeta[];
  points: UserDailyPoint[];
}

export interface AdminDashboardData {
  period: { type: AdminDashboardPeriod; label: string };
  kpis: AdminKPI;
  daily_costs: DailyCostPoint[];
  user_daily: UserDailySeries;
  user_usage: UserUsageRow[];
  quota_requests: QuotaRequest[];
}

export interface UserUsageDetailProject {
  id: string;
  title: string;
  status: string;
  cost_usd: number;
  completed_at: string | null;
  created_at: string;
}

export interface UserUsageDetail {
  user_id: string;
  name: string;
  email: string;
  role: UserRoleType;
  period: { type: AdminDashboardPeriod; label: string };
  total_cost_usd: number;
  limit_usd: number;
  reports_total: number;
  reports_completed: number;
  reports_in_progress: number;
  projects: UserUsageDetailProject[];
  daily_costs: DailyCostPoint[];
}

const NOW = new Date("2026-05-27T00:00:00Z");

function dailyDate(offsetDays: number): string {
  const d = new Date(NOW);
  d.setUTCDate(d.getUTCDate() - offsetDays);
  return d.toISOString().slice(0, 10);
}

const DAILY_BASE = [
  41, 58, 32, 67, 49, 28, 35, 73, 81, 62, 44, 38, 55, 69, 71, 48, 36, 52, 64, 77, 89, 58, 41, 33,
  47, 66, 72, 84, 95, 78,
];

const DAILY_USERS = [
  9, 11, 8, 12, 10, 7, 9, 12, 13, 11, 10, 8, 10, 12, 12, 9, 8, 10, 11, 12, 13, 10, 9, 7, 8, 11, 12,
  13, 13, 12,
];

// offset = NOW으로부터 며칠 전인지(0 = 오늘). 각 period가 덮는 [oldest, newest] offset 구간을 반환.
function offsetRangeForPeriod(period: AdminDashboardPeriod): { oldest: number; newest: number } {
  const thisMonthStartOffset = NOW.getUTCDate() - 1;
  if (period === "this_month") return { oldest: thisMonthStartOffset, newest: 0 };
  if (period === "last_7_days") return { oldest: 6, newest: 0 };
  if (period === "last_30_days") return { oldest: 29, newest: 0 };
  // last_month: 이번 달 1일 바로 전날(저번 달 마지막날)부터 저번 달 1일까지
  const daysInLastMonth = new Date(
    Date.UTC(NOW.getUTCFullYear(), NOW.getUTCMonth(), 0),
  ).getUTCDate();
  return {
    oldest: thisMonthStartOffset + daysInLastMonth,
    newest: thisMonthStartOffset + 1,
  };
}

function dailyCostsForPeriod(period: AdminDashboardPeriod): DailyCostPoint[] {
  const { oldest, newest } = offsetRangeForPeriod(period);
  const points: DailyCostPoint[] = [];
  for (let offset = oldest; offset >= newest; offset--) {
    const idx = offset % DAILY_BASE.length;
    points.push({
      date: dailyDate(offset),
      cost_usd: DAILY_BASE[idx] ?? 50,
      users: DAILY_USERS[idx] ?? 10,
    });
  }
  return points;
}

export const ADMIN_USAGE: UserUsageRow[] = [
  {
    user_id: "u_admin_001",
    name: "최재웅",
    email: "admin@loweninsight.kr",
    role: "admin",
    tokens_used: 4_200_000,
    cost_usd: 52,
    limit_usd: 100,
    last_active: "2026-05-27T10:42:00Z",
  },
  {
    user_id: "u_worker_001",
    name: "박지영",
    email: "jiyoung.park@loweninsight.kr",
    role: "worker",
    tokens_used: 12_800_000,
    cost_usd: 187,
    limit_usd: 200,
    last_active: "2026-05-27T09:14:00Z",
  },
  {
    user_id: "u_worker_002",
    name: "이수민",
    email: "sumin.lee@loweninsight.kr",
    role: "worker",
    tokens_used: 28_500_000,
    cost_usd: 356,
    limit_usd: 300,
    last_active: "2026-05-26T18:55:00Z",
  },
  {
    user_id: "u_worker_003",
    name: "정현우",
    email: "hyunwoo.jung@loweninsight.kr",
    role: "worker",
    tokens_used: 8_100_000,
    cost_usd: 98,
    limit_usd: 150,
    last_active: "2026-05-27T11:08:00Z",
  },
  {
    user_id: "u_worker_004",
    name: "최영서",
    email: "youngseo.choi@loweninsight.kr",
    role: "worker",
    tokens_used: 5_300_000,
    cost_usd: 67,
    limit_usd: 100,
    last_active: "2026-05-26T16:32:00Z",
  },
  {
    user_id: "u_worker_005",
    name: "강민지",
    email: "minji.kang@loweninsight.kr",
    role: "worker",
    tokens_used: 3_700_000,
    cost_usd: 41,
    limit_usd: 100,
    last_active: "2026-05-27T08:21:00Z",
  },
  {
    user_id: "u_viewer_001",
    name: "한도윤",
    email: "doyoon.han@loweninsight.kr",
    role: "viewer",
    tokens_used: 800_000,
    cost_usd: 9,
    limit_usd: 50,
    last_active: "2026-05-25T14:02:00Z",
  },
  {
    user_id: "u_worker_006",
    name: "윤서연",
    email: "seoyeon.yun@loweninsight.kr",
    role: "worker",
    tokens_used: 6_900_000,
    cost_usd: 78,
    limit_usd: 100,
    last_active: "2026-05-27T07:45:00Z",
  },
];

export const QUOTA_REQUESTS: QuotaRequest[] = [
  {
    id: "qr_001",
    user_id: "u_worker_001",
    user_name: "박지영",
    amount_usd: 50,
    reason: "긴급 보고서 마감 (광역교통망 예타조사)",
    requested_at: "2026-05-27T09:16:00Z",
    status: "pending",
  },
  {
    id: "qr_002",
    user_id: "u_worker_002",
    user_name: "이수민",
    amount_usd: 100,
    reason: "다중 프로젝트 동시 진행으로 한도 초과",
    requested_at: "2026-05-26T19:02:00Z",
    status: "pending",
  },
];

const TOP_N = 6;

// 일별 총비용을 상위 N명 + '기타'로 쪼갠 사용자별 누적 시리즈(전체 비중 기준 안분).
function userDailyFromDaily(daily: DailyCostPoint[]): UserDailySeries {
  const top = [...ADMIN_USAGE].sort((a, b) => b.cost_usd - a.cost_usd).slice(0, TOP_N);
  const share = top.reduce((s, u) => s + u.cost_usd, 0) || 1;
  const users: UserSeriesMeta[] = top.map((u) => ({ key: u.user_id, name: u.name }));
  users.push({ key: "other", name: "기타" });
  const points: UserDailyPoint[] = daily.map((p) => {
    const costs: Record<string, number> = {};
    let assigned = 0;
    for (const u of top) {
      const c = Math.round(p.cost_usd * (u.cost_usd / share) * 100) / 100;
      costs[u.user_id] = c;
      assigned += c;
    }
    costs.other = Math.max(0, Math.round((p.cost_usd - assigned) * 100) / 100);
    return { date: p.date, costs };
  });
  return { users, points };
}

// custom 구간: start~end(포함) 일별 시리즈를 DAILY_BASE 순환으로 생성.
function dailyCostsBetween(startISO: string, endISO: string): DailyCostPoint[] {
  const points: DailyCostPoint[] = [];
  const start = new Date(`${startISO}T00:00:00Z`).getTime();
  const end = new Date(`${endISO}T00:00:00Z`).getTime();
  let i = 0;
  for (let t = start; t <= end; t += 86_400_000) {
    const idx = i % DAILY_BASE.length;
    points.push({
      date: new Date(t).toISOString().slice(0, 10),
      cost_usd: DAILY_BASE[idx] ?? 50,
      users: DAILY_USERS[idx] ?? 10,
    });
    i++;
  }
  return points;
}

export function buildAdminDashboardFixture(
  period: AdminDashboardPeriod,
  custom?: { start: string; end: string },
): AdminDashboardData {
  const daily_costs =
    period === "custom" && custom
      ? dailyCostsBetween(custom.start, custom.end)
      : dailyCostsForPeriod(period === "custom" ? "last_30_days" : period);
  let label: string;
  if (period === "custom" && custom) {
    label = `${custom.start} ~ ${custom.end}`;
  } else {
    const { oldest, newest } = offsetRangeForPeriod(period === "custom" ? "last_30_days" : period);
    label = `${dailyDate(oldest)} ~ ${dailyDate(newest)}`;
  }
  const total_cost_usd = daily_costs.reduce((sum, p) => sum + p.cost_usd, 0);

  return {
    period: { type: period, label },
    kpis: {
      total_cost_usd,
      cost_limit_usd: 3_000,
      online_users: 4,
      active_users: 13,
      active_projects: 7,
      completed_reports: 4,
    },
    daily_costs,
    user_daily: userDailyFromDaily(daily_costs),
    user_usage: ADMIN_USAGE,
    quota_requests: QUOTA_REQUESTS,
  };
}

export const ADMIN_DASHBOARD: AdminDashboardData = buildAdminDashboardFixture("last_30_days");

export function buildUserUsageDetailFixture(
  userId: string,
  period: AdminDashboardPeriod,
): UserUsageDetail {
  const u = ADMIN_USAGE.find((x) => x.user_id === userId) ?? ADMIN_USAGE[0];
  if (!u) throw new Error("no demo users");
  const resolved = period === "custom" ? "last_30_days" : period;
  const daily_costs: DailyCostPoint[] = dailyCostsForPeriod(resolved).map((p) => ({
    date: p.date,
    cost_usd: Math.round(p.cost_usd * (u.cost_usd / 400) * 100) / 100,
    users: 1,
  }));
  const total = Math.round(daily_costs.reduce((s, p) => s + p.cost_usd, 0) * 100) / 100;
  const { oldest, newest } = offsetRangeForPeriod(resolved);
  return {
    user_id: u.user_id,
    name: u.name,
    email: u.email,
    role: u.role,
    period: { type: period, label: `${dailyDate(oldest)} ~ ${dailyDate(newest)}` },
    total_cost_usd: total,
    limit_usd: u.limit_usd,
    reports_total: 5,
    reports_completed: 3,
    reports_in_progress: 1,
    projects: [
      {
        id: "p1",
        title: "숏폼 콘텐츠 시장 분석",
        status: "completed",
        cost_usd: Math.round(total * 0.4 * 100) / 100,
        completed_at: "2026-05-20T00:00:00Z",
        created_at: "2026-05-10T00:00:00Z",
      },
      {
        id: "p2",
        title: "원격근무 실태 조사",
        status: "writing",
        cost_usd: Math.round(total * 0.35 * 100) / 100,
        completed_at: null,
        created_at: "2026-05-22T00:00:00Z",
      },
      {
        id: "p3",
        title: "고령화 대응 정책 보고서",
        status: "completed",
        cost_usd: Math.round(total * 0.25 * 100) / 100,
        completed_at: "2026-05-25T00:00:00Z",
        created_at: "2026-05-18T00:00:00Z",
      },
    ],
    daily_costs,
  };
}

export function decideQuotaRequest(
  id: string,
  decision: "approved" | "rejected",
): QuotaRequest | null {
  const idx = QUOTA_REQUESTS.findIndex((q) => q.id === id);
  if (idx < 0) return null;
  const current = QUOTA_REQUESTS[idx];
  if (!current) return null;
  const updated: QuotaRequest = { ...current, status: decision };
  QUOTA_REQUESTS[idx] = updated;
  return updated;
}
