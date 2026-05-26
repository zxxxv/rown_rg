import { HttpResponse, http } from "msw";
import { ADMIN_DASHBOARD, decideQuotaRequest } from "@/api/mock/fixtures/admin";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

interface ApproveBody {
  decision: "approved" | "rejected";
}

export const adminHandlers = [
  http.get(url("admin/dashboard"), () => {
    return HttpResponse.json({ data: ADMIN_DASHBOARD }, { status: 200 });
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
