import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";

export const SettingItemSchema = z.object({
  key: z.string(),
  label: z.string(),
  group: z.string(),
  kind: z.enum(["str", "number", "bool"]),
  is_secret: z.boolean(),
  configured: z.boolean(),
  source: z.enum(["db", "env", "none"]),
  value: z.string().nullable(),
});
export type SettingItem = z.infer<typeof SettingItemSchema>;

export const SettingsResponseSchema = z.object({ items: z.array(SettingItemSchema) });
export type SettingsResponse = z.infer<typeof SettingsResponseSchema>;

export const settingsKeys = {
  all: ["settings"] as const,
};

export async function getSettings(): Promise<SettingItem[]> {
  const data = await apiClient.get<unknown>("admin/settings");
  return SettingsResponseSchema.parse(data).items;
}

export function useSettings() {
  return useQuery({ queryKey: settingsKeys.all, queryFn: getSettings });
}

export async function updateSetting(key: string, value: string): Promise<SettingItem> {
  const data = await apiClient.put<unknown>(`admin/settings/${key}`, { json: { value } });
  return SettingItemSchema.parse(data);
}

export function useUpdateSetting() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...settingsKeys.all, "update"],
    mutationFn: ({ key, value }: { key: string; value: string }) => updateSetting(key, value),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: settingsKeys.all });
    },
  });
}
