import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";
import { libraryKeys } from "@/api/library";

export const PromptKindSchema = z.enum(["agent", "rule"]);
export type PromptKind = z.infer<typeof PromptKindSchema>;

export const PersonalPromptSchema = z.object({
  id: z.string(),
  kind: PromptKindSchema,
  name: z.string(),
  content: z.string(),
  base_ref: z.string().nullish(),
  cat: z.string().nullish(),
  description: z.string().nullish(),
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
});
export type SystemPrompt = z.infer<typeof SystemPromptSchema>;

export const promptKeys = {
  all: ["prompts"] as const,
  personal: (id: string) => [...promptKeys.all, "personal", id] as const,
  system: (kind: string, ref: string) => [...promptKeys.all, "system", kind, ref] as const,
};

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
}

export function useCreatePersonalPrompt() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...promptKeys.all, "create"],
    mutationFn: async (body: CreatePersonalPromptBody) => {
      const data = await apiClient.post<unknown>("prompts/personal", { json: body });
      return PersonalPromptSchema.parse(data);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: libraryKeys.all });
      void qc.invalidateQueries({ queryKey: promptKeys.all });
    },
  });
}

export function useUpdatePersonalPrompt(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...promptKeys.personal(id), "update"],
    mutationFn: async (body: { name?: string; content?: string }) => {
      const data = await apiClient.patch<unknown>(`prompts/personal/${id}`, { json: body });
      return PersonalPromptSchema.parse(data);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: promptKeys.personal(id) });
      void qc.invalidateQueries({ queryKey: libraryKeys.all });
    },
  });
}

export function useDeletePersonalPrompt() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...promptKeys.all, "delete"],
    mutationFn: async (id: string) => {
      await apiClient.delete<void>(`prompts/personal/${id}`);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: libraryKeys.all });
      void qc.invalidateQueries({ queryKey: promptKeys.all });
    },
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
