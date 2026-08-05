import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/api/client";
import type {
  AdminDashboardData,
  AdminDashboardPeriod,
  QuotaRequest,
  UserUsageDetail,
} from "@/api/mock/fixtures/admin";

export interface AdminDashboardParams {
  period?: AdminDashboardPeriod;
  // period=custom일 때 구간(YYYY-MM-DD). 프리셋에서는 무시된다.
  start?: string;
  end?: string;
}

export const adminKeys = {
  all: ["admin"] as const,
  dashboard: (params: AdminDashboardParams = {}) =>
    [...adminKeys.all, "dashboard", params] as const,
  userUsage: (userId: string, params: AdminDashboardParams = {}) =>
    [...adminKeys.all, "user-usage", userId, params] as const,
};

function periodSearchParams(params: AdminDashboardParams): Record<string, string> {
  const sp: Record<string, string> = {};
  if (params.period) sp.period = params.period;
  if (params.period === "custom" && params.start && params.end) {
    sp.start = params.start;
    sp.end = params.end;
  }
  return sp;
}

export async function getAdminDashboard(
  params: AdminDashboardParams = {},
): Promise<AdminDashboardData> {
  return apiClient.get<AdminDashboardData>("admin/dashboard", {
    searchParams: periodSearchParams(params),
  });
}

export function useAdminDashboard(params: AdminDashboardParams = {}) {
  // custom인데 구간이 아직 안 채워졌으면 조회를 미룬다(불완전 요청 방지).
  const ready = params.period !== "custom" || Boolean(params.start && params.end);
  return useQuery({
    queryKey: adminKeys.dashboard(params),
    queryFn: () => getAdminDashboard(params),
    placeholderData: keepPreviousData,
    enabled: ready,
  });
}

export async function getUserUsageDetail(
  userId: string,
  params: AdminDashboardParams = {},
): Promise<UserUsageDetail> {
  return apiClient.get<UserUsageDetail>(`admin/users/${userId}/usage`, {
    searchParams: periodSearchParams(params),
  });
}

/** 사용자 클릭 상세 — userId가 null이면 비활성(다이얼로그 닫힘 상태). */
export function useUserUsageDetail(userId: string | null, params: AdminDashboardParams = {}) {
  return useQuery({
    queryKey: adminKeys.userUsage(userId ?? "", params),
    queryFn: () => getUserUsageDetail(userId as string, params),
    enabled: Boolean(userId),
  });
}

export interface ApproveQuotaInput {
  requestId: string;
  decision: "approved" | "rejected";
}

export function useApproveQuotaExtension() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...adminKeys.all, "approve-quota"],
    mutationFn: async (input: ApproveQuotaInput): Promise<QuotaRequest> => {
      return apiClient.post<QuotaRequest>(`admin/quota-requests/${input.requestId}/decide`, {
        json: { decision: input.decision },
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: adminKeys.all });
    },
  });
}
