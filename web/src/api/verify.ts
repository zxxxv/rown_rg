import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";

// 실계약: GET /projects/{id}/verify-report — PM 검증 경고 리포트(차단 아님).
// assemble 직후 챕터당 1콜 pm_verify가 저장한 문서 횡단 일관성 경고.
// 아직 검증 전이거나 경고가 없으면 빈 배열이 온다.
export const VerifyFindingSchema = z.object({
  id: z.string(),
  chapter_number: z.number().int(),
  severity: z.enum(["critical", "warning"]).catch("warning"),
  category: z.string(),
  section_ref: z.string().nullish(),
  detail: z.string(),
  created_at: z.string(),
});
export type VerifyFinding = z.infer<typeof VerifyFindingSchema>;

export const VerifyReportSchema = z.array(VerifyFindingSchema);

export const verifyKeys = {
  all: ["verify-report"] as const,
  report: (projectId: string) => [...verifyKeys.all, projectId] as const,
};

export async function getVerifyReport(projectId: string): Promise<VerifyFinding[]> {
  const data = await apiClient.get<unknown>(`projects/${projectId}/verify-report`);
  return VerifyReportSchema.parse(data);
}

export function useVerifyReport(projectId: string, enabled = true) {
  return useQuery({
    queryKey: verifyKeys.report(projectId),
    queryFn: () => getVerifyReport(projectId),
    enabled: Boolean(projectId) && enabled,
  });
}
