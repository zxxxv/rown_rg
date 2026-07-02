import { useMutation, useQuery } from "@tanstack/react-query";
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
  // confirm_password 는 클라이언트 검증용 — 서버에는 보내지 않는다.
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
