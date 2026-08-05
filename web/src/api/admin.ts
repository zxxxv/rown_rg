import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/api/client";
import type {
  AdminDashboardData,
  AdminDashboardPeriod,
  QuotaRequest,
} from "@/api/mock/fixtures/admin";

export interface AdminDashboardParams {
  period?: AdminDashboardPeriod;
}

export const adminKeys = {
  all: ["admin"] as const,
  dashboard: (params: AdminDashboardParams = {}) =>
    [...adminKeys.all, "dashboard", params] as const,
};

export async function getAdminDashboard(
  params: AdminDashboardParams = {},
): Promise<AdminDashboardData> {
  const searchParams: Record<string, string> = {};
  if (params.period) searchParams.period = params.period;
  return apiClient.get<AdminDashboardData>("admin/dashboard", { searchParams });
}

export function useAdminDashboard(params: AdminDashboardParams = {}) {
  return useQuery({
    queryKey: adminKeys.dashboard(params),
    queryFn: () => getAdminDashboard(params),
    placeholderData: keepPreviousData,
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
