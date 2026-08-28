// 자료 사용 통계 - 완성 보고서가 어떤 자료 위에 서 있는지(전체/장/절 3레벨).
// 백엔드 src/api/schemas/source_stats.py와 짝. counts 키는 참고문헌 전역 번호다.

import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";

export const SourceUsageItemSchema = z.object({
  number: z.number(),
  source_id: z.string(),
  title: z.string(),
  url: z.string().nullable(),
  origin: z.string(),
  reliability: z.string().nullable(),
  citations: z.number(),
  sections_used: z.number(),
  published_year: z.number().nullish(),
});
export type SourceUsageItem = z.infer<typeof SourceUsageItemSchema>;

const CountsSchema = z.record(z.string(), z.number());

export const ChapterUsageSchema = z.object({
  chapter_number: z.number(),
  title: z.string(),
  citations: z.number(),
  counts: CountsSchema,
});
export type ChapterUsage = z.infer<typeof ChapterUsageSchema>;

export const SectionUsageSchema = z.object({
  section_id: z.string(),
  chapter_number: z.number(),
  section_number: z.number(),
  title: z.string(),
  citations: z.number(),
  n_sources: z.number(),
  counts: CountsSchema,
});
export type SectionUsage = z.infer<typeof SectionUsageSchema>;

export const SourceUsageResponseSchema = z.object({
  total_citations: z.number(),
  sources: z.array(SourceUsageItemSchema),
  chapters: z.array(ChapterUsageSchema),
  sections: z.array(SectionUsageSchema),
  unused: z.array(SourceUsageItemSchema),
});
export type SourceUsageResponse = z.infer<typeof SourceUsageResponseSchema>;

export async function getSourceUsage(projectId: string): Promise<SourceUsageResponse> {
  const data = await apiClient.get<unknown>(`projects/${projectId}/source-usage`);
  return SourceUsageResponseSchema.parse(data);
}

export function useSourceUsage(projectId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["source-usage", projectId],
    queryFn: () => getSourceUsage(projectId),
    enabled: Boolean(projectId) && enabled,
    // 완성 보고서의 통계는 편집 전까지 불변에 가깝다 - 재조회로 흔들 이유가 없다
    staleTime: 60_000,
    retry: false,
  });
}

// 서술 구성 통계 - 본문 문장이 어디까지 근거로 받쳐지는지(전체/장).
// 백엔드 source_stats.EvidenceCompositionResponse와 짝. 판정 칸은 근거 패널의
// claimTone과 같은 기준이라 패널 숫자와 일치한다.

export const EvidenceTallySchema = z.object({
  claims: z.number(),
  confirmed: z.number(),
  crosslingual: z.number().default(0),
  unconfirmed: z.number(),
  uncited: z.number(),
  defect: z.number(),
  uncovered: z.number(),
});
export type EvidenceTally = z.infer<typeof EvidenceTallySchema>;

export const ChapterEvidenceCompositionSchema = EvidenceTallySchema.extend({
  chapter_number: z.number(),
  title: z.string(),
});
export type ChapterEvidenceComposition = z.infer<typeof ChapterEvidenceCompositionSchema>;

export const EvidenceCompositionResponseSchema = z.object({
  total: EvidenceTallySchema,
  chapters: z.array(ChapterEvidenceCompositionSchema),
});
export type EvidenceCompositionResponse = z.infer<typeof EvidenceCompositionResponseSchema>;

export async function getEvidenceComposition(
  projectId: string,
): Promise<EvidenceCompositionResponse> {
  const data = await apiClient.get<unknown>(`projects/${projectId}/stats/evidence-composition`);
  return EvidenceCompositionResponseSchema.parse(data);
}

export function useEvidenceComposition(projectId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["evidence-composition", projectId],
    queryFn: () => getEvidenceComposition(projectId),
    enabled: Boolean(projectId) && enabled,
    // 첫 계산이 절당 1~2초라 서버가 캐시한다 - 클라이언트도 흔들 이유가 없다.
    staleTime: 300_000,
    retry: false,
  });
}
