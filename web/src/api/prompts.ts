import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { analystKeys } from "@/api/analysts";
import { apiClient } from "@/api/client";
import { libraryKeys } from "@/api/library";

export const PromptKindSchema = z.enum(["agent", "rule"]);
export type PromptKind = z.infer<typeof PromptKindSchema>;

/** 목표 분량 3단 - 에이전트에만 쓰인다. 절 분량과 분할 파트 수를 정하는 값. */
export const PromptVolumeSchema = z.enum(["short", "normal", "long"]);
export type PromptVolume = z.infer<typeof PromptVolumeSchema>;

export const PromptSpecSchema = z.object({
  /** 목표 분량(자). 둘 다 비면 지정 없음 - 덮어쓴 시스템 에이전트의 원본 값을 승계한다 */
  min_chars: z.number().int().nullish(),
  max_chars: z.number().int().nullish(),
  /** 3단 버튼 시절의 레거시 값(예전에 저장된 것만 읽는다) */
  volume: PromptVolumeSchema.nullish(),
  queries: z.array(z.string()).default([]),
  /** 폼의 칸 값(임무·분석 방법론·핵심 산출물·구성 지침). 서버가 이걸로 본문을 조합한다 */
  sections: z.record(z.string(), z.string()).default({}),
});
export type PromptSpec = z.infer<typeof PromptSpecSchema>;

export const PersonalPromptSchema = z.object({
  id: z.string(),
  kind: PromptKindSchema,
  name: z.string(),
  content: z.string(),
  base_ref: z.string().nullish(),
  cat: z.string().nullish(),
  description: z.string().nullish(),
  spec: PromptSpecSchema.default({ queries: [], sections: {} }),
  updated_at: z.string(),
});
export type PersonalPrompt = z.infer<typeof PersonalPromptSchema>;

export const SystemPromptSchema = z.object({
  ref: z.string(),
  kind: PromptKindSchema,
  name: z.string(),
  content: z.string(),
  cat: z.string().nullish(),
  description: z.string().nullish(),
  /** 서버가 분해해 준 칸 값 - '복제해서 시작'이 폼을 바로 채운다 */
  sections: z.record(z.string(), z.string()).default({}),
  min_chars: z.number().int().nullish(),
  max_chars: z.number().int().nullish(),
});
export type SystemPrompt = z.infer<typeof SystemPromptSchema>;

export const promptKeys = {
  all: ["prompts"] as const,
  personal: (id: string) => [...promptKeys.all, "personal", id] as const,
  personalList: (kind: string) => [...promptKeys.all, "personal-list", kind] as const,
  system: (kind: string, ref: string) => [...promptKeys.all, "system", kind, ref] as const,
  systemList: (kind: string) => [...promptKeys.all, "system-list", kind] as const,
};

/** 개인 프롬프트가 바뀌면 되돌아봐야 하는 캐시 전부.
 *
 * 목록(personalList)·상세(personal)·라이브러리 트리·목차 편집기의 담당 에이전트
 * 칩(/analysts)이 모두 이 데이터를 보여준다. 하나라도 빠뜨리면 "고쳤는데 화면은
 * 그대로"가 된다 - 수정이 상세 키만 지워 목록이 새로고침 전까지 옛 값이었다
 * (2026-08-10 사용자 지적).
 */
function invalidatePrompts(qc: ReturnType<typeof useQueryClient>): void {
  void qc.invalidateQueries({ queryKey: promptKeys.all });
  void qc.invalidateQueries({ queryKey: libraryKeys.all });
  void qc.invalidateQueries({ queryKey: analystKeys.all });
}

// ─── 목록 (전용 관리 페이지용) ───────────────────────────────────────────────

export function useListPersonalPrompts(kind?: PromptKind) {
  return useQuery({
    queryKey: promptKeys.personalList(kind ?? "all"),
    queryFn: async () => {
      const data = await apiClient.get<unknown>("prompts/personal", {
        searchParams: kind ? { kind } : {},
      });
      return z.array(PersonalPromptSchema).parse(data);
    },
  });
}

export function useListSystemPrompts(kind?: PromptKind) {
  return useQuery({
    queryKey: promptKeys.systemList(kind ?? "all"),
    queryFn: async () => {
      const data = await apiClient.get<unknown>("prompts/system", {
        searchParams: kind ? { kind } : {},
      });
      return z.array(SystemPromptSchema).parse(data);
    },
  });
}

// ─── 개인 프롬프트 (편집 가능) ───────────────────────────────────────────────

export async function getPersonalPrompt(id: string): Promise<PersonalPrompt> {
  const data = await apiClient.get<unknown>(`prompts/personal/${id}`);
  return PersonalPromptSchema.parse(data);
}

export function usePersonalPrompt(id: string, enabled = true) {
  return useQuery({
    queryKey: promptKeys.personal(id),
    queryFn: () => getPersonalPrompt(id),
    enabled: enabled && Boolean(id),
  });
}

export interface CreatePersonalPromptBody {
  kind: PromptKind;
  name: string;
  content: string;
  base_ref?: string | null;
  cat?: string | null;
  description?: string | null;
  spec?: PromptSpecInput;
}

/** 저장 시 보내는 구조화 설정 - sections를 주면 서버가 본문을 조합한다 */
export interface PromptSpecInput {
  min_chars?: number;
  max_chars?: number;
  queries?: string[];
  sections?: Record<string, string>;
}

export function useCreatePersonalPrompt() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...promptKeys.all, "create"],
    mutationFn: async (body: CreatePersonalPromptBody) => {
      const data = await apiClient.post<unknown>("prompts/personal", { json: body });
      return PersonalPromptSchema.parse(data);
    },
    onSuccess: () => invalidatePrompts(qc),
  });
}

export function useUpdatePersonalPrompt(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...promptKeys.personal(id), "update"],
    mutationFn: async (body: {
      name?: string;
      content?: string;
      cat?: string | null;
      description?: string | null;
      spec?: PromptSpecInput;
    }) => {
      const data = await apiClient.patch<unknown>(`prompts/personal/${id}`, { json: body });
      return PersonalPromptSchema.parse(data);
    },
    onSuccess: () => invalidatePrompts(qc),
  });
}

export function useDeletePersonalPrompt() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...promptKeys.all, "delete"],
    mutationFn: async (id: string) => {
      await apiClient.delete<void>(`prompts/personal/${id}`);
    },
    onSuccess: () => invalidatePrompts(qc),
  });
}

// ─── 시스템 프롬프트 (읽기전용) ──────────────────────────────────────────────

export async function getSystemPrompt(kind: string, ref: string): Promise<SystemPrompt> {
  const data = await apiClient.get<unknown>(`prompts/system/${kind}/${encodeURIComponent(ref)}`);
  return SystemPromptSchema.parse(data);
}

export function useSystemPrompt(kind: string, ref: string, enabled = true) {
  return useQuery({
    queryKey: promptKeys.system(kind, ref),
    queryFn: () => getSystemPrompt(kind, ref),
    enabled: enabled && Boolean(ref),
  });
}
