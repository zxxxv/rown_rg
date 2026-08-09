import { useMutation, useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";
import { type ChangePasswordInput, type MyTokenUsage, MyTokenUsageSchema } from "@/api/types";

export const profileKeys = {
  all: ["profile"] as const,
  tokenUsage: () => [...profileKeys.all, "token-usage"] as const,
};

export async function getMyTokenUsage(): Promise<MyTokenUsage> {
  const data = await apiClient.get<unknown>("users/me/token-usage");
  return MyTokenUsageSchema.parse(data);
}

export function useMyTokenUsage() {
  return useQuery({
    queryKey: profileKeys.tokenUsage(),
    queryFn: getMyTokenUsage,
    staleTime: 5 * 60 * 1000,
  });
}

export async function changePassword(input: ChangePasswordInput): Promise<void> {
  // confirm_password 는 클라이언트 검증용 - 서버에는 보내지 않는다.
  await apiClient.post<void>("auth/change-password", {
    json: {
      current_password: input.current_password,
      new_password: input.new_password,
    },
  });
}

export function useChangePassword() {
  return useMutation({
    mutationKey: [...profileKeys.all, "change-password"],
    mutationFn: (input: ChangePasswordInput) => changePassword(input),
  });
}

// ─── 한도 증액 신청 (POST /users/me/quota-requests) ─────────────────────
// 백엔드 CreateLimitRequestInput: amount_usd(>0), reason(1~2000자) → LimitRequestRead

export const QuotaRequestInputSchema = z.object({
  amount_usd: z.coerce.number().positive("증액 금액은 0보다 커야 합니다"),
  reason: z.string().min(1, "사유를 입력하세요").max(2000, "사유는 2000자 이내여야 합니다"),
});
export type QuotaRequestInput = z.infer<typeof QuotaRequestInputSchema>;

export const QuotaRequestReadSchema = z.object({
  id: z.string(),
  user_id: z.string(),
  user_name: z.string(),
  amount_usd: z.coerce.number(),
  reason: z.string(),
  requested_at: z.string(),
  status: z.enum(["pending", "approved", "rejected"]),
});
export type QuotaRequestRead = z.infer<typeof QuotaRequestReadSchema>;

export async function createQuotaRequest(input: QuotaRequestInput): Promise<QuotaRequestRead> {
  const data = await apiClient.post<unknown>("users/me/quota-requests", { json: input });
  return QuotaRequestReadSchema.parse(data);
}

export function useCreateQuotaRequest() {
  return useMutation({
    mutationKey: [...profileKeys.all, "quota-request"],
    mutationFn: createQuotaRequest,
  });
}
