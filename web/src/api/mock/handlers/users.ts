import { HttpResponse, http } from "msw";
import { ADMIN_USAGE } from "@/api/mock/fixtures/admin";
import { DEMO_ADMIN_USERS } from "@/api/mock/fixtures/admin-users";
import type { UserRoleType } from "@/api/types";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

function notFound() {
  return HttpResponse.json(
    { error: { code: "USER_NOT_FOUND", message: "사용자를 찾을 수 없습니다" } },
    { status: 404 },
  );
}

interface UpdateUserBody {
  name?: string;
  role?: UserRoleType;
  is_active?: boolean;
}

interface ResetPasswordBody {
  new_password?: string;
}

interface SetQuotaBody {
  monthly_limit_usd?: number;
}

// 백엔드 password_handler 정책과 동일: 12자 이상 + 대문자·소문자·숫자·특수문자
function violatesPasswordPolicy(pw: string): string | null {
  if (pw.length < 12) return "비밀번호는 12자 이상이어야 합니다";
  if (!/[A-Z]/.test(pw)) return "대문자를 1자 이상 포함해야 합니다";
  if (!/[a-z]/.test(pw)) return "소문자를 1자 이상 포함해야 합니다";
  if (!/[0-9]/.test(pw)) return "숫자를 1자 이상 포함해야 합니다";
  if (!/[^A-Za-z0-9]/.test(pw)) return "특수문자를 1자 이상 포함해야 합니다";
  return null;
}

export const usersHandlers = [
  // GET /users?limit&offset - 관리자 목록 (백엔드는 총계 없이 배열만 반환)
  http.get(url("users"), ({ request }) => {
    const u = new URL(request.url);
    const limit = Math.max(1, Math.min(100, Number(u.searchParams.get("limit") ?? "20")));
    const offset = Math.max(0, Number(u.searchParams.get("offset") ?? "0"));
    const items = DEMO_ADMIN_USERS.slice(offset, offset + limit);
    return HttpResponse.json({ data: items }, { status: 200 });
  }),

  // PATCH /users/{id} - 이름·역할·활성 부분 수정
  http.patch(url("users/:id"), async ({ params, request }) => {
    const id = String(params.id);
    const user = DEMO_ADMIN_USERS.find((x) => x.id === id);
    if (!user) return notFound();

    const body = (await request.json()) as UpdateUserBody;
    if (body.name !== undefined) user.name = body.name;
    if (body.role !== undefined) user.role = body.role;
    if (body.is_active !== undefined) user.is_active = body.is_active;
    user.updated_at = new Date().toISOString();

    // 대시보드 픽스처와 역할 동기화
    const usage = ADMIN_USAGE.find((x) => x.user_id === id);
    if (usage && body.role !== undefined) usage.role = body.role;

    return HttpResponse.json({ data: user }, { status: 200 });
  }),

  // POST /admin/users/{id}/unlock - 잠금 해제
  http.post(url("admin/users/:id/unlock"), ({ params }) => {
    const id = String(params.id);
    const user = DEMO_ADMIN_USERS.find((x) => x.id === id);
    if (!user) return notFound();
    user.locked_until = null;
    user.updated_at = new Date().toISOString();
    return HttpResponse.json({ data: { detail: "계정 잠금이 해제되었습니다" } }, { status: 200 });
  }),

  // POST /admin/users/{id}/reset-password - 비밀번호 재설정(정책 검증)
  http.post(url("admin/users/:id/reset-password"), async ({ params, request }) => {
    const id = String(params.id);
    const user = DEMO_ADMIN_USERS.find((x) => x.id === id);
    if (!user) return notFound();

    const body = (await request.json()) as ResetPasswordBody;
    const pw = body.new_password ?? "";
    const violation = violatesPasswordPolicy(pw);
    if (violation) {
      return HttpResponse.json(
        { error: { code: "WEAK_PASSWORD", message: violation } },
        { status: 422 },
      );
    }
    user.has_password = true;
    user.locked_until = null;
    user.password_changed_at = new Date().toISOString();
    user.updated_at = user.password_changed_at;
    return HttpResponse.json(
      { data: { detail: "비밀번호가 재설정되었습니다 (기존 세션 전체 종료)" } },
      { status: 200 },
    );
  }),

  // PATCH /admin/users/{id}/quota - 월 한도 지정
  http.patch(url("admin/users/:id/quota"), async ({ params, request }) => {
    const id = String(params.id);
    const body = (await request.json()) as SetQuotaBody;
    const limit = Number(body.monthly_limit_usd);
    if (!Number.isFinite(limit) || limit < 0) {
      return HttpResponse.json(
        {
          error: { code: "validation_failed", message: "monthly_limit_usd는 0 이상이어야 합니다" },
        },
        { status: 422 },
      );
    }
    // 사용자 관리·대시보드 어느 쪽 픽스처에도 없는 id면 404
    const user = DEMO_ADMIN_USERS.find((x) => x.id === id);
    const usage = ADMIN_USAGE.find((x) => x.user_id === id);
    if (!user && !usage) return notFound();

    if (usage) usage.limit_usd = limit;
    return HttpResponse.json(
      {
        data: {
          user_id: id,
          monthly_limit_usd: limit,
          updated_at: new Date().toISOString(),
        },
      },
      { status: 200 },
    );
  }),
];
