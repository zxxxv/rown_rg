import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";

// 실계약: GET /presets → [{id, name, desc, n_chapters, n_sections}]
// 자유 주제(프리셋 없음)는 목록에 없으며 생성 시 preset=null 로 표현한다.
export const PresetReadSchema = z.object({
  id: z.string(),
  name: z.string(),
  desc: z.string(),
  n_chapters: z.number().int().nonnegative(),
  n_sections: z.number().int().nonnegative(),
});
export type PresetRead = z.infer<typeof PresetReadSchema>;

export const PresetsResponseSchema = z.array(PresetReadSchema);
export type PresetsResponse = z.infer<typeof PresetsResponseSchema>;

export const presetKeys = {
  all: ["presets"] as const,
  list: () => [...presetKeys.all, "list"] as const,
};

export async function getPresets(): Promise<PresetsResponse> {
  const data = await apiClient.get<unknown>("presets");
  return PresetsResponseSchema.parse(data);
}

export function usePresets() {
  return useQuery({
    queryKey: presetKeys.list(),
    queryFn: getPresets,
    staleTime: Infinity,
  });
}
