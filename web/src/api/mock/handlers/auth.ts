import { HttpResponse, http } from "msw";
import { DEMO_ADMIN_USER, DEMO_CREDENTIALS } from "@/api/mock/fixtures/users";
import { env } from "@/env";

const MAX_FAILURES = 5;

let isLoggedIn = false;
let failureCount = 0;

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

interface LoginBody {
  id: string;
  password: string;
}

export const authHandlers = [
  http.post(url("auth/login"), async ({ request }) => {
    if (failureCount >= MAX_FAILURES) {
      return HttpResponse.json(
        {
          error: {
            code: "account_locked",
            message: "5회 이상 로그인에 실패하여 계정이 잠겼습니다. 관리자에게 문의하세요.",
          },
        },
        { status: 423 },
      );
    }

    const body = (await request.json()) as LoginBody;
    if (body.id === DEMO_CREDENTIALS.id && body.password === DEMO_CREDENTIALS.password) {
      isLoggedIn = true;
      failureCount = 0;
      return HttpResponse.json({ data: { user: DEMO_ADMIN_USER } }, { status: 200 });
    }

    failureCount += 1;
    return HttpResponse.json(
      {
        error: {
          code: "invalid_credentials",
          message: "사번/이메일 또는 비밀번호가 올바르지 않습니다.",
        },
      },
      { status: 401 },
    );
  }),

  http.get(url("auth/me"), () => {
    if (!isLoggedIn) {
      return HttpResponse.json(
        { error: { code: "unauthorized", message: "로그인이 필요합니다." } },
        { status: 401 },
      );
    }
    return HttpResponse.json({ data: DEMO_ADMIN_USER }, { status: 200 });
  }),

  http.post(url("auth/logout"), () => {
    isLoggedIn = false;
    return HttpResponse.json({ data: null }, { status: 200 });
  }),
];
