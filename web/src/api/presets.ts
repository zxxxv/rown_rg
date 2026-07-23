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

// 실계약: GET /presets/{key} → 전체 골격(챕터·섹션·방향·핵심포인트·담당 에이전트)
// 생성 화면 목차 편집기의 초기값. agents는 분석 에이전트 name 참조.
export const PresetSectionDetailSchema = z.object({
  title: z.string(),
  direction: z.string().default(""),
  key_points: z.array(z.string()).default([]),
  agents: z.array(z.string()).default([]),
});
export type PresetSectionDetail = z.infer<typeof PresetSectionDetailSchema>;

export const PresetChapterDetailSchema = z.object({
  title: z.string(),
  sections: z.array(PresetSectionDetailSchema),
});
export type PresetChapterDetail = z.infer<typeof PresetChapterDetailSchema>;

export const PresetDetailSchema = z.object({
  id: z.string(),
  name: z.string(),
  desc: z.string(),
  domain_context: z.string().default(""),
  chapters: z.array(PresetChapterDetailSchema),
});
export type PresetDetail = z.infer<typeof PresetDetailSchema>;

export const presetKeys = {
  all: ["presets"] as const,
  list: () => [...presetKeys.all, "list"] as const,
  detail: (key: string) => [...presetKeys.all, "detail", key] as const,
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

export async function getPresetDetail(key: string): Promise<PresetDetail> {
  // 프리셋 id는 한글(예: 예비타당성조사) — URL 인코딩 필수.
  const data = await apiClient.get<unknown>(`presets/${encodeURIComponent(key)}`);
  return PresetDetailSchema.parse(data);
}

export function usePresetDetail(key: string | null) {
  return useQuery({
    queryKey: presetKeys.detail(key ?? "__none__"),
    queryFn: () => getPresetDetail(key as string),
    enabled: Boolean(key),
    staleTime: Infinity,
  });
}
