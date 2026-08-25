import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";

// 실계약: GET /projects/{id}/drift - 설계 변경이 아직 본문에 닿지 않은 절("미반영").
// 보고서는 완성 순간이 끝이 아니다(2026-08-25) - 완료 뒤에도 목차를 고치거나 자료를
// 빼면, 그 변경이 본문에 반영되기 전까지 여기에 뜬다. 본문이 틀렸다는 뜻이 아니라
// '그때 계약대로 쓰였고 그 뒤 계약이 바뀌었다'는 뜻이다.
// 서버는 판정만 한다 - 재작성은 사람이 고른다(절당 실측 $0.67).

export const DRIFT_REASON_LABEL: Record<string, string> = {
  plan_changed: "목차 수정 미반영",
  source_excluded: "자료 제외 미반영",
  missing: "본문 없음",
};

export const DriftSectionSchema = z.object({
  section_id: z.string(),
  /** "2.3 인구·고령화 영향" - 사람이 읽는 좌표 */
  label: z.string(),
  reasons: z.array(z.string()).default([]),
  excluded_sources: z.array(z.object({ id: z.string(), title: z.string() })).default([]),
});
export type DriftSection = z.infer<typeof DriftSectionSchema>;

export const DriftSchema = z.object({
  sections: z.array(DriftSectionSchema).default([]),
  n_plan_changed: z.number().int().default(0),
  n_source_excluded: z.number().int().default(0),
  n_missing: z.number().int().default(0),
});
export type Drift = z.infer<typeof DriftSchema>;

export const driftKeys = {
  all: ["drift"] as const,
  detail: (projectId: string) => [...driftKeys.all, projectId] as const,
};

export function useDrift(projectId: string, enabled = true) {
  return useQuery({
    queryKey: driftKeys.detail(projectId),
    queryFn: async () => {
      const data = await apiClient.get<unknown>(`projects/${projectId}/drift`);
      return DriftSchema.parse(data);
    },
    enabled: Boolean(projectId) && enabled,
    staleTime: 15_000,
  });
}
