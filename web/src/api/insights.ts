import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";

// 실계약: GET /projects/{id}/insights - 시사점 2~3쪽 요약.
// 조립 시 LLM 1콜이 시사점·제언 절을 압축해 저장한다. 원본 보고서와 별개 산출물이라
// HWPX에는 실리지 않는다(2026-08-25 결정) - 이 화면에서만 본다.
// content=null은 오류가 아니라 '아직 없음'이다(조립 전이거나 생성 실패).
export const InsightsSchema = z.object({
  content: z.string().nullable(),
  /** 요약의 원천이 된 절 라벨 - 무엇을 압축한 것인지 화면에 밝힌다 */
  source_sections: z.array(z.string()).default([]),
  model: z.string().nullable(),
  running: z.boolean().default(false),
});
export type Insights = z.infer<typeof InsightsSchema>;

export const insightsKeys = {
  all: ["insights"] as const,
  detail: (projectId: string) => [...insightsKeys.all, projectId] as const,
};

export function useInsights(projectId: string, enabled = true) {
  return useQuery({
    queryKey: insightsKeys.detail(projectId),
    queryFn: async () => {
      const data = await apiClient.get<unknown>(`projects/${projectId}/insights`);
      return InsightsSchema.parse(data);
    },
    enabled: Boolean(projectId) && enabled,
    // 재생성이 도는 동안만 따라간다 - 끝나면 스스로 멈춘다(재검증과 같은 규약).
    refetchInterval: (query) => (query.state.data?.running ? 4000 : false),
  });
}

export function useRebuildInsights(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...insightsKeys.detail(projectId), "rebuild"],
    mutationFn: () => apiClient.post<unknown>(`projects/${projectId}/insights`, { json: {} }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: insightsKeys.detail(projectId) });
    },
  });
}
