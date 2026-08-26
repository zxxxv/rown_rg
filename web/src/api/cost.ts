import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";

// 실계약: GET /projects/{id}/cost-basis - 절 하나에 얼마나 드는가(이 보고서 실측).
//
// 일반 단가를 안 쓰는 이유: 실측에서 절당 비용이 프로젝트마다 $0.40~$1.34로 3.4배
// 벌어졌다(모델 등급·절 분량·재료 양이 다 다르다). 하나의 평균을 박으면 어떤
// 보고서에서는 3배 과소, 어떤 보고서에서는 3배 과대가 된다.
// 개수를 곱하는 것은 화면 몫이다 - 고르는 절이 바뀔 때마다 요청을 다시 보내지 않는다.

export const CostBasisSchema = z.object({
  /** null = 아직 모른다(한 번도 안 쓴 보고서). 지어낸 숫자를 보여주지 않는다. */
  per_section_usd: z.number().nullable().default(null),
  n_sections_measured: z.number().int().default(0),
  /** project(이 보고서 실측) | model(같은 등급 다른 보고서 평균) | none */
  basis: z.string().default("none"),
  spent_usd: z.number().default(0),
});
export type CostBasis = z.infer<typeof CostBasisSchema>;

export const costKeys = {
  basis: (projectId: string) => ["cost-basis", projectId] as const,
};

export function useCostBasis(projectId: string, enabled = true) {
  return useQuery({
    queryKey: costKeys.basis(projectId),
    queryFn: async () => {
      const data = await apiClient.get<unknown>(`projects/${projectId}/cost-basis`);
      return CostBasisSchema.parse(data);
    },
    enabled: Boolean(projectId) && enabled,
    staleTime: 60_000,
  });
}

/** "$2.11" - 소액이라 센트까지 보여야 의미가 있다. 모르면 null. */
export function formatUsd(v: number | null | undefined): string | null {
  if (v === null || v === undefined || Number.isNaN(v)) return null;
  return `$${v.toFixed(2)}`;
}

/** 절 N개를 다시 쓸 때의 예상 비용 문구 - 근거까지 밝힌다(추정의 출처를 숨기지 않는다). */
export function estimateLabel(basis: CostBasis | undefined, nSections: number): string | null {
  if (!basis || basis.per_section_usd === null || nSections <= 0) return null;
  const total = formatUsd(basis.per_section_usd * nSections);
  const unit = formatUsd(basis.per_section_usd);
  const source = basis.basis === "project" ? "이 보고서 실측" : "비슷한 보고서 평균";
  return `예상 ${total} · 절당 ${unit} (${source})`;
}
