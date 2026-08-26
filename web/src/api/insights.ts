import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";

// 실계약: GET /projects/{id}/insights - 시사점 2~3쪽 요약.
// 조립 시 LLM 1콜이 시사점·제언 절을 압축해 저장한다. 원본 보고서와 별개 산출물이라
// 본문 HWPX에는 실리지 않고(2026-08-25 결정), 요약만 담은 한글 파일로 따로 받는다
// (2026-08-27, GET /projects/{id}/insights/export).
// content=null은 오류가 아니라 '아직 없음'이다(조립 전이거나 생성 실패).
export const InsightsSchema = z.object({
  content: z.string().nullable(),
  /** 요약의 원천이 된 절 라벨 - 무엇을 압축한 것인지 화면에 밝힌다 */
  source_sections: z.array(z.string()).default([]),
  model: z.string().nullable(),
  /** 만든 시각(UTC ISO) - 온도 0이라 같은 본문이면 같은 요약이 나온다.
   *  '다시 만들기'가 실제로 돌았는지는 이 값으로만 드러난다. */
  built_at: z.string().nullable().default(null),
  running: z.boolean().default(false),
  /** 근거로 고른 절(절 안정 id) - 체크 상태의 정본. 비었으면 자동 선택으로 돈다 */
  selected_section_ids: z.array(z.string()).default([]),
  /** 고를 수 있는 절 전부 - 목록을 따로 부르지 않게 함께 온다 */
  selectable: z
    .array(
      z.object({
        section_id: z.string(),
        label: z.string(),
        chars: z.number().int().default(0),
      }),
    )
    .default([]),
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
    // sectionIds=undefined면 저장된 선택 그대로, []면 선택을 지우고 자동으로 되돌린다.
    mutationFn: (sectionIds?: string[]) =>
      apiClient.post<unknown>(`projects/${projectId}/insights`, {
        json: sectionIds === undefined ? {} : { section_ids: sectionIds },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: insightsKeys.detail(projectId) });
    },
  });
}
