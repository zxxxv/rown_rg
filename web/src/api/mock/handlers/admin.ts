import { HttpResponse, http } from "msw";
import type { AdminDashboardPeriod } from "@/api/mock/fixtures/admin";
import {
  buildAdminDashboardFixture,
  buildUserUsageDetailFixture,
  decideQuotaRequest,
} from "@/api/mock/fixtures/admin";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

interface ApproveBody {
  decision: "approved" | "rejected";
}

const VALID_PERIODS: readonly AdminDashboardPeriod[] = [
  "this_month",
  "last_month",
  "last_7_days",
  "last_30_days",
  "custom",
];

function isValidPeriod(value: string): value is AdminDashboardPeriod {
  return (VALID_PERIODS as readonly string[]).includes(value);
}

// 기본은 이번 달(실서버 기본과 동일). custom은 start/end 둘 다 있어야 유효.
function resolvePeriod(u: URL): { period: AdminDashboardPeriod; start?: string; end?: string } {
  const raw = u.searchParams.get("period") ?? "this_month";
  const period = isValidPeriod(raw) ? raw : "this_month";
  const start = u.searchParams.get("start") ?? undefined;
  const end = u.searchParams.get("end") ?? undefined;
  return { period, start, end };
}

export const adminHandlers = [
  http.get(url("admin/dashboard"), ({ request }) => {
    const { period, start, end } = resolvePeriod(new URL(request.url));
    if (period === "custom" && (!start || !end)) {
      return HttpResponse.json(
        { error: { code: "INVALID_CUSTOM_RANGE", message: "start·end가 필요합니다." } },
        { status: 422 },
      );
    }
    const custom = period === "custom" && start && end ? { start, end } : undefined;
    return HttpResponse.json({ data: buildAdminDashboardFixture(period, custom) }, { status: 200 });
  }),

  http.get(url("admin/users/:uid/usage"), ({ params, request }) => {
    const { period } = resolvePeriod(new URL(request.url));
    return HttpResponse.json(
      { data: buildUserUsageDetailFixture(String(params.uid), period) },
      { status: 200 },
    );
  }),

  http.post(url("admin/quota-requests/:rid/decide"), async ({ params, request }) => {
    const rid = String(params.rid);
    const body = (await request.json()) as ApproveBody;
    const updated = decideQuotaRequest(rid, body.decision);
    if (!updated) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "요청을 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    return HttpResponse.json({ data: updated }, { status: 200 });
  }),
];
