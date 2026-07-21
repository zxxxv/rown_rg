import { HttpResponse, http } from "msw";
import { QUOTA_REQUESTS } from "@/api/mock/fixtures/admin";
import { MY_TOKEN_USAGE } from "@/api/mock/fixtures/profile";
import { DEMO_ADMIN_USER, DEMO_CREDENTIALS } from "@/api/mock/fixtures/users";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

interface ChangePasswordBody {
  current_password: string;
  new_password: string;
}

interface QuotaRequestBody {
  amount_usd?: number;
  reason?: string;
}

export const profileHandlers = [
  http.get(url("users/me/token-usage"), () => {
    return HttpResponse.json({ data: MY_TOKEN_USAGE }, { status: 200 });
  }),

  http.post(url("auth/change-password"), async ({ request }) => {
    const body = (await request.json()) as ChangePasswordBody;

    // 데모: 현재 비밀번호가 DEMO 계정 비밀번호와 일치해야 통과
    if (body.current_password !== DEMO_CREDENTIALS.password) {
      return HttpResponse.json(
        {
          error: {
            code: "invalid_credentials",
            message: "현재 비밀번호가 올바르지 않습니다.",
          },
        },
        { status: 401 },
      );
    }
    if (!body.new_password || body.new_password.length < 8) {
      return HttpResponse.json(
        {
          error: {
            code: "weak_password",
            message: "새 비밀번호는 8자 이상이어야 합니다.",
          },
        },
        { status: 422 },
      );
    }
    return HttpResponse.json({ data: { success: true } }, { status: 200 });
  }),

  // POST /users/me/quota-requests — 한도 증액 신청(pending 생성)
  http.post(url("users/me/quota-requests"), async ({ request }) => {
    const body = (await request.json()) as QuotaRequestBody;
    const amount = Number(body.amount_usd);
    const reason = (body.reason ?? "").trim();
    if (!Number.isFinite(amount) || amount <= 0 || reason.length < 1 || reason.length > 2000) {
      return HttpResponse.json(
        {
          error: {
            code: "validation_failed",
            message: "amount_usd는 양수, reason은 1~2000자여야 합니다.",
          },
        },
        { status: 422 },
      );
    }
    const created = {
      id: `qr_${crypto.randomUUID().slice(0, 8)}`,
      user_id: DEMO_ADMIN_USER.id,
      user_name: DEMO_ADMIN_USER.name,
      amount_usd: amount,
      reason,
      requested_at: new Date().toISOString(),
      status: "pending" as const,
    };
    // 관리자 대시보드 "한도 초과 승인 요청" 패널에 바로 노출되도록 픽스처에 반영
    QUOTA_REQUESTS.unshift(created);
    return HttpResponse.json({ data: created }, { status: 201 });
  }),
];
