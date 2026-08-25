import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";

// 실계약: GET /presets → [{id, name, desc, n_chapters, n_sections, scope}]
// 자유 주제(프리셋 없음)는 목록에 없으며 생성 시 preset=null 로 표현한다.
// scope='personal'은 내가 저장한 목차 프리셋(id="u:<uuid>") - 시스템과 같은 경로로 로드된다.
export const PresetReadSchema = z.object({
  id: z.string(),
  name: z.string(),
  desc: z.string(),
  // 이 유형이 무엇에 쓰는 보고서인지 한 줄(백엔드 domain_context 첫 문장). 개인·공유
  // 프리셋에는 없다. nullish: 이 필드 도입 전 응답과 목킹 호환.
  summary: z.string().nullish(),
  n_chapters: z.number().int().nonnegative(),
  n_sections: z.number().int().nonnegative(),
  // shared = 남이 공개한 프리셋. 골라 쓸 수는 있고 고치려면 가져와야 한다.
  scope: z.enum(["system", "personal", "shared"]).default("system"),
  owner_name: z.string().nullish(),
  updated_at: z.string().nullish(),
  // personal일 때 내 프리셋의 공개 여부 - 목록에서 바로 토글한다.
  is_public: z.boolean().default(false),
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
  builds_on: z.array(z.string()).default([]),
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
  // 프리셋 id는 한글(예: 예비타당성조사) - URL 인코딩 필수.
  const data = await apiClient.get<unknown>(`presets/${encodeURIComponent(key)}`);
  return PresetDetailSchema.parse(data);
}

export function usePresetDetail(key: string | null) {
  return useQuery({
    queryKey: presetKeys.detail(key ?? "__none__"),
    queryFn: () => getPresetDetail(key as string),
    enabled: Boolean(key),
    // 내 프리셋("u:")은 편집으로 바뀔 수 있어 무한 캐시 금지 - 시스템은 파일이라 불변.
    staleTime: key?.startsWith("u:") ? 0 : Infinity,
  });
}

// ─── 내 목차 프리셋 (개인 저장) ──────────────────────────────────────────────
// 목차 편집기 구성을 이름 붙여 저장하고 다음 프로젝트에서 재사용한다.

export const UserPresetSchema = z.object({
  id: z.string(),
  /** 생성 폼 preset 값으로 그대로 쓰는 "u:<uuid>" */
  key: z.string(),
  name: z.string(),
  description: z.string().nullish(),
  n_chapters: z.number().int().nonnegative(),
  n_sections: z.number().int().nonnegative(),
  /** 켜면 사내 전원의 프리셋 선택지에 뜬다 */
  is_public: z.boolean().default(false),
  updated_at: z.string(),
});
export type UserPreset = z.infer<typeof UserPresetSchema>;

export interface UserPresetBody {
  name: string;
  description?: string | null;
  chapters: PresetChapterDetail[];
  is_public?: boolean;
}

export function useCreateUserPreset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: UserPresetBody) => {
      const data = await apiClient.post<unknown>("presets/personal", { json: body });
      return UserPresetSchema.parse(data);
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: presetKeys.all }),
  });
}

export function useUpdateUserPreset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, ...body }: UserPresetBody & { id: string }) => {
      const data = await apiClient.put<unknown>(`presets/personal/${id}`, { json: body });
      return UserPresetSchema.parse(data);
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: presetKeys.all }),
  });
}

/** 목록에서 바로 켜고 끄는 공개 토글 - PUT은 목차 전체를 요구해 토글용으로 못 쓴다. */
export function useSetPresetVisibility() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, isPublic }: { id: string; isPublic: boolean }) => {
      const data = await apiClient.patch<unknown>(`presets/personal/${id}/visibility`, {
        json: { is_public: isPublic },
      });
      return UserPresetSchema.parse(data);
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: presetKeys.all }),
  });
}

/** 남이 공개한 목차 프리셋을 내 것으로 복제한다(비공개 사본). 바로 쓰는 건 목록에서
 * 고르면 되고, 이건 **고쳐 쓸 때**를 위한 것이다. */
export function useImportSharedPreset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (sourceId: string) => {
      const data = await apiClient.post<unknown>(`presets/personal/import/${sourceId}`);
      return UserPresetSchema.parse(data);
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: presetKeys.all }),
  });
}

export function useDeleteUserPreset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      await apiClient.delete<void>(`presets/personal/${id}`);
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: presetKeys.all }),
  });
}
