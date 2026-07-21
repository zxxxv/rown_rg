import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";

// 백엔드 src/api/schemas/admin.py IpWhitelistRead 기준 (super_admin 전용 라우터)

export const IpWhitelistEntrySchema = z.object({
  id: z.string(),
  ip_cidr: z.string(),
  description: z.string().nullable(),
  is_active: z.boolean(),
  expires_at: z.string().nullable(),
  created_by: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type IpWhitelistEntry = z.infer<typeof IpWhitelistEntrySchema>;

export const IpWhitelistListSchema = z.array(IpWhitelistEntrySchema);

export const ipWhitelistKeys = {
  all: ["ip-whitelist"] as const,
  list: () => [...ipWhitelistKeys.all, "list"] as const,
};

export async function getIpWhitelist(): Promise<IpWhitelistEntry[]> {
  const data = await apiClient.get<unknown>("admin/ip-whitelist");
  return IpWhitelistListSchema.parse(data);
}

export function useIpWhitelist() {
  return useQuery({
    queryKey: ipWhitelistKeys.list(),
    queryFn: getIpWhitelist,
  });
}

export interface CreateIpEntryInput {
  ip_cidr: string;
  description?: string;
  /** ISO(UTC) 문자열 — datetime-local 값은 전송 전 toISOString()으로 변환한다. */
  expires_at?: string;
}

export async function createIpEntry(input: CreateIpEntryInput): Promise<IpWhitelistEntry> {
  const data = await apiClient.post<unknown>("admin/ip-whitelist", {
    json: {
      ip_cidr: input.ip_cidr,
      description: input.description ?? null,
      expires_at: input.expires_at ?? null,
    },
  });
  return IpWhitelistEntrySchema.parse(data);
}

export function useCreateIpEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...ipWhitelistKeys.all, "create"],
    mutationFn: createIpEntry,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ipWhitelistKeys.all });
    },
  });
}

export interface UpdateIpEntryInput {
  entryId: string;
  description?: string | null;
  is_active?: boolean;
  expires_at?: string | null;
}

export async function updateIpEntry(input: UpdateIpEntryInput): Promise<IpWhitelistEntry> {
  const { entryId, ...body } = input;
  const data = await apiClient.patch<unknown>(`admin/ip-whitelist/${entryId}`, { json: body });
  return IpWhitelistEntrySchema.parse(data);
}

export function useUpdateIpEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...ipWhitelistKeys.all, "update"],
    mutationFn: updateIpEntry,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ipWhitelistKeys.all });
    },
  });
}

export async function deleteIpEntry(entryId: string): Promise<{ detail: string }> {
  return apiClient.delete<{ detail: string }>(`admin/ip-whitelist/${entryId}`);
}

export function useDeleteIpEntry() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...ipWhitelistKeys.all, "delete"],
    mutationFn: deleteIpEntry,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ipWhitelistKeys.all });
    },
  });
}
