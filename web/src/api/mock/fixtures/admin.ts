import type { UserRoleType } from "@/api/types";

export interface AdminKPI {
  total_cost_usd: number;
  cost_limit_usd: number;
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

export interface AdminDashboardData {
  period: { type: "this_month"; label: string };
  kpis: AdminKPI;
  daily_costs: DailyCostPoint[];
  user_usage: UserUsageRow[];
  quota_requests: QuotaRequest[];
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

const DAILY_COSTS: DailyCostPoint[] = DAILY_BASE.map((cost, i) => ({
  date: dailyDate(29 - i),
  cost_usd: cost,
  users: DAILY_USERS[i] ?? 10,
}));

const TOTAL_COST = DAILY_BASE.reduce((s, v) => s + v, 0);

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

export const ADMIN_DASHBOARD: AdminDashboardData = {
  period: { type: "this_month", label: "2026-05" },
  kpis: {
    total_cost_usd: TOTAL_COST,
    cost_limit_usd: 3_000,
    active_users: 13,
    active_projects: 7,
    completed_reports: 4,
  },
  daily_costs: DAILY_COSTS,
  user_usage: ADMIN_USAGE,
  quota_requests: QUOTA_REQUESTS,
};

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
