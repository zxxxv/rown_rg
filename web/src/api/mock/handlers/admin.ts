import { HttpResponse, http } from "msw";
import type { AdminDashboardPeriod } from "@/api/mock/fixtures/admin";
import { buildAdminDashboardFixture, decideQuotaRequest } from "@/api/mock/fixtures/admin";
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
];

function isValidPeriod(value: string): value is AdminDashboardPeriod {
  return (VALID_PERIODS as readonly string[]).includes(value);
}

export const adminHandlers = [
  http.get(url("admin/dashboard"), ({ request }) => {
    const raw = new URL(request.url).searchParams.get("period") ?? "this_month";
    const period = isValidPeriod(raw) ? raw : "this_month";
    return HttpResponse.json({ data: buildAdminDashboardFixture(period) }, { status: 200 });
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
