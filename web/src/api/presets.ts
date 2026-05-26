import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";
import { PresetSchema, ProjectConfigSchema } from "@/api/types";

export const PresetItemSchema = z.object({
  preset: PresetSchema,
  label: z.string(),
  description: z.string(),
  defaults: ProjectConfigSchema,
});
export type PresetItem = z.infer<typeof PresetItemSchema>;

export const PresetsResponseSchema = z.object({
  items: z.array(PresetItemSchema),
});
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
