import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";

// 실계약: GET /analysts → 분석 에이전트 카탈로그 (섹션별 담당 배정 UI 선택지)
// 백엔드 src/prompts 파일 카탈로그가 단일 진실 - 프롬프트 본문은 내려오지 않는다.
export const AnalystSchema = z.object({
  id: z.string(),
  name: z.string(),
  cat: z.string(),
  desc: z.string(),
  // volume_target 분량 안내 (예: "10~15" 페이지). 없는 에이전트도 있다.
  pages: z.string().nullish(),
});
export type Analyst = z.infer<typeof AnalystSchema>;

export const AnalystsResponseSchema = z.array(AnalystSchema);

export const analystKeys = {
  all: ["analysts"] as const,
  list: () => [...analystKeys.all, "list"] as const,
};

export async function getAnalysts(): Promise<Analyst[]> {
  const data = await apiClient.get<unknown>("analysts");
  return AnalystsResponseSchema.parse(data);
}

export function useAnalysts() {
  return useQuery({
    queryKey: analystKeys.list(),
    queryFn: getAnalysts,
    staleTime: Number.POSITIVE_INFINITY,
  });
}
