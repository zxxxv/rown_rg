import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";
import { invalidateSection, sectionKeys } from "@/api/sections";
import { SectionContentResponseSchema } from "@/api/types";

// 실계약: /projects/{id}/sections/{sid}/variants - 한 절을 여러 벌 뽑아 놓고 고른다.
//
// 재작성은 한 번에 하나만 준다. 마음에 안 들면 다시 눌러야 하고, 그러면 방금 것은
// 사라진다 - 둘을 나란히 놓고 고를 수가 없었다. 실제로 사람이 하는 일은 "이게 나은가
// 저게 나은가"인데 화면이 그걸 못 하게 막고 있었다.
// 값이 안 개수에 곱해지므로(절당 실측 $0.4~$1.3) 기본 2·최대 4다 - 지금 본문까지
// 합쳐 셋을 견준다.

export const SectionVariantSchema = z.object({
  id: z.string(),
  content: z.string(),
  n_chars: z.number().int().default(0),
  n_markers: z.number().int().default(0),
  evidence_count: z.number().int().default(0),
  /** 재료가 모자라 분량 목표를 내린 안 - 짧은 이유를 알아야 고를 수 있다 */
  volume_scaled: z.boolean().default(false),
});
export type SectionVariant = z.infer<typeof SectionVariantSchema>;

export const SectionVariantsSchema = z.object({
  running: z.boolean().default(false),
  total: z.number().int().default(0),
  done: z.number().int().default(0),
  failures: z.record(z.string(), z.string()).default({}),
  variants: z.array(SectionVariantSchema).default([]),
});
export type SectionVariants = z.infer<typeof SectionVariantsSchema>;

const variantKey = (projectId: string, sectionId: string) =>
  [...sectionKeys.content(projectId, sectionId), "variants"] as const;

export function useSectionVariants(projectId: string, sectionId: string, enabled = true) {
  return useQuery({
    queryKey: variantKey(projectId, sectionId),
    queryFn: async () => {
      const data = await apiClient.get<unknown>(
        `projects/${projectId}/sections/${sectionId}/variants`,
      );
      return SectionVariantsSchema.parse(data);
    },
    enabled: Boolean(projectId) && Boolean(sectionId) && enabled,
    // 완성되는 대로 쌓이므로 도는 동안 따라간다 - 셋을 다 기다릴 필요가 없다.
    refetchInterval: (query) => (query.state.data?.running ? 3000 : false),
  });
}

export function useStartVariants(projectId: string, sectionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...variantKey(projectId, sectionId), "start"],
    mutationFn: (input: { n: number; instruction?: string }) =>
      apiClient.post<unknown>(`projects/${projectId}/sections/${sectionId}/variants`, {
        json: { n: input.n, instruction: input.instruction ?? "" },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: variantKey(projectId, sectionId) });
    },
  });
}

export function useDiscardVariants(projectId: string, sectionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...variantKey(projectId, sectionId), "discard"],
    mutationFn: () =>
      apiClient.delete<unknown>(`projects/${projectId}/sections/${sectionId}/variants`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: variantKey(projectId, sectionId) });
    },
  });
}

/** 고른 안을 본문으로 - 서버는 재작성과 같은 반영 경로를 지난다(인용 재매핑·버전). */
export function useAdoptVariant(projectId: string, sectionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...variantKey(projectId, sectionId), "adopt"],
    mutationFn: async (variantId: string) => {
      const data = await apiClient.post<unknown>(
        `projects/${projectId}/sections/${sectionId}/variants/${variantId}/adopt`,
        { json: {} },
      );
      return SectionContentResponseSchema.parse(data);
    },
    onSuccess: () => {
      // 재작성과 **같은 무효화**를 쓴다. 여기에 손으로 목록을 적어 두었더니 근거 대조와
      // PM 경고 캐시가 빠져, 안을 채택해 본문이 248자로 바뀐 뒤에도 머리글이 옛 본문의
      // "본문 120줄 중 83줄을 대조 · 무근거 수치 9"를 그대로 띄웠다(2026-08-27 실측).
      void invalidateSection(qc, projectId, sectionId);
      void qc.invalidateQueries({ queryKey: variantKey(projectId, sectionId) });
      void qc.invalidateQueries({ queryKey: ["drift", projectId] });
    },
  });
}
