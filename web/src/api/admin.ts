import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/api/client";
import type { AdminDashboardData, QuotaRequest } from "@/api/mock/fixtures/admin";

export const adminKeys = {
  all: ["admin"] as const,
  dashboard: () => [...adminKeys.all, "dashboard"] as const,
};

export async function getAdminDashboard(): Promise<AdminDashboardData> {
  return apiClient.get<AdminDashboardData>("admin/dashboard");
}

export function useAdminDashboard() {
  return useQuery({
    queryKey: adminKeys.dashboard(),
    queryFn: getAdminDashboard,
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
      void qc.invalidateQueries({ queryKey: adminKeys.dashboard() });
    },
  });
}
