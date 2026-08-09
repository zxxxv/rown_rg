import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { adminKeys } from "@/api/admin";
import { apiClient } from "@/api/client";
import { UserRoleSchema } from "@/api/types";

// ─── 스키마 (백엔드 src/api/schemas/user.py UserRead 기준) ──────────────

export const AdminUserSchema = z.object({
  id: z.string(),
  email: z.string(),
  name: z.string(),
  role: UserRoleSchema,
  is_active: z.boolean(),
  has_password: z.boolean(),
  last_login_at: z.string().nullable(),
  password_changed_at: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
  // 주의: 백엔드 UserRead에는 아직 노출되지 않는 필드(DB에는 존재).
  // 실서버 응답에는 없으므로 optional - 잠금 해제 버튼은 값이 있을 때만 활성화된다.
  locked_until: z.string().nullable().optional(),
});
export type AdminUser = z.infer<typeof AdminUserSchema>;

export const AdminUserListSchema = z.array(AdminUserSchema);
// 백엔드 GET /users는 {items, total} 페이지네이션(UserListResponse)을 반환한다.
// 과거·목업은 배열만 주므로 둘 다 수용한다(버전 스큐 방어).
export const AdminUserListResponseSchema = z.union([
  AdminUserListSchema,
  z.object({ items: AdminUserListSchema, total: z.number().int().nonnegative() }),
]);

export interface UsersListParams {
  limit?: number;
  offset?: number;
  q?: string;
}

export const userKeys = {
  all: ["users"] as const,
  list: (params: UsersListParams) => [...userKeys.all, "list", params] as const,
};

// ─── 조회 ────────────────────────────────────────────────────────────────

export async function getUsers(params: UsersListParams = {}): Promise<AdminUser[]> {
  const searchParams: Record<string, string> = {};
  if (params.limit !== undefined) searchParams.limit = String(params.limit);
  if (params.offset !== undefined) searchParams.offset = String(params.offset);
  if (params.q) searchParams.q = params.q;
  const data = await apiClient.get<unknown>("users", { searchParams });
  const parsed = AdminUserListResponseSchema.parse(data);
  return Array.isArray(parsed) ? parsed : parsed.items;
}

export function useUsersList(params: UsersListParams = {}) {
  return useQuery({
    queryKey: userKeys.list(params),
    queryFn: () => getUsers(params),
  });
}

// ─── 수정 (PATCH /users/{id}) ────────────────────────────────────────────

export interface UpdateUserInput {
  userId: string;
  name?: string;
  role?: AdminUser["role"];
  is_active?: boolean;
}

export async function updateUser(input: UpdateUserInput): Promise<AdminUser> {
  const { userId, ...body } = input;
  const data = await apiClient.patch<unknown>(`users/${userId}`, { json: body });
  return AdminUserSchema.parse(data);
}

export function useUpdateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...userKeys.all, "update"],
    mutationFn: updateUser,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: userKeys.all });
      void qc.invalidateQueries({ queryKey: adminKeys.dashboard() });
    },
  });
}

// ─── 관리 액션 (admin 라우터) ────────────────────────────────────────────

export interface ActionResult {
  detail: string;
}

export async function unlockUser(userId: string): Promise<ActionResult> {
  return apiClient.post<ActionResult>(`admin/users/${userId}/unlock`);
}

export function useUnlockUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...userKeys.all, "unlock"],
    mutationFn: unlockUser,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: userKeys.all });
    },
  });
}

export interface ResetPasswordInput {
  userId: string;
  new_password: string;
}

export async function resetUserPassword(input: ResetPasswordInput): Promise<ActionResult> {
  return apiClient.post<ActionResult>(`admin/users/${input.userId}/reset-password`, {
    json: { new_password: input.new_password },
  });
}

export function useResetUserPassword() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...userKeys.all, "reset-password"],
    mutationFn: resetUserPassword,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: userKeys.all });
    },
  });
}

export const UserLimitReadSchema = z.object({
  user_id: z.string(),
  monthly_limit_usd: z.coerce.number().nonnegative(),
  updated_at: z.string(),
});
export type UserLimitRead = z.infer<typeof UserLimitReadSchema>;

export interface SetUserQuotaInput {
  userId: string;
  monthly_limit_usd: number;
}

export async function setUserQuota(input: SetUserQuotaInput): Promise<UserLimitRead> {
  const data = await apiClient.patch<unknown>(`admin/users/${input.userId}/quota`, {
    json: { monthly_limit_usd: input.monthly_limit_usd },
  });
  return UserLimitReadSchema.parse(data);
}

export function useSetUserQuota() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...userKeys.all, "set-quota"],
    mutationFn: setUserQuota,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: userKeys.all });
      void qc.invalidateQueries({ queryKey: adminKeys.dashboard() });
    },
  });
}
