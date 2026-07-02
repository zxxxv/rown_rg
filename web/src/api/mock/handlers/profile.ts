import { HttpResponse, http } from "msw";
import { MY_TOKEN_USAGE } from "@/api/mock/fixtures/profile";
import { DEMO_CREDENTIALS } from "@/api/mock/fixtures/users";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

interface ChangePasswordBody {
  current_password: string;
  new_password: string;
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
];
