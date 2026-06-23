import { useMutation, useQuery } from "@tanstack/react-query";
import { apiClient } from "@/api/client";
import {
  type LoginInput,
  type LoginResponse,
  LoginResponseSchema,
  type MeResponse,
  MeResponseSchema,
} from "@/api/types";

export const authKeys = {
  all: ["auth"] as const,
  me: () => [...authKeys.all, "me"] as const,
};

export async function login(input: LoginInput): Promise<LoginResponse> {
  const data = await apiClient.post<unknown>("auth/login", { json: input });
  return LoginResponseSchema.parse(data);
}

export async function logout(): Promise<void> {
  await apiClient.post<void>("auth/logout");
}

export async function me(): Promise<MeResponse> {
  const data = await apiClient.get<unknown>("auth/me");
  return MeResponseSchema.parse(data);
}

export function useLogin() {
  return useMutation({
    mutationKey: [...authKeys.all, "login"],
    mutationFn: (input: LoginInput) => login(input),
  });
}

export function useMe() {
  return useQuery({
    queryKey: authKeys.me(),
    queryFn: me,
    retry: false,
    staleTime: 5 * 60 * 1000,
  });
}
