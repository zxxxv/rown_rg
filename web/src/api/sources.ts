import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/api/client";
import {
  type Source,
  type SourceListResponse,
  SourceListResponseSchema,
  SourceSchema,
} from "@/api/types";

export const sourceKeys = {
  all: ["sources"] as const,
  list: (projectId: string) => [...sourceKeys.all, "list", projectId] as const,
};

export async function getProjectSources(projectId: string): Promise<SourceListResponse> {
  const data = await apiClient.get<unknown>(`projects/${projectId}/sources`);
  return SourceListResponseSchema.parse(data);
}

export function useProjectSources(projectId: string) {
  return useQuery({
    queryKey: sourceKeys.list(projectId),
    queryFn: () => getProjectSources(projectId),
    enabled: Boolean(projectId),
  });
}

export interface PatchSourceInput {
  sid: string;
  is_included: boolean | null;
}

export function usePatchSource(projectId: string) {
  const qc = useQueryClient();
  const key = sourceKeys.list(projectId);
  return useMutation({
    mutationFn: async ({ sid, is_included }: PatchSourceInput) => {
      const data = await apiClient.patch<unknown>(`projects/${projectId}/sources/${sid}`, {
        json: { is_included },
      });
      return SourceSchema.parse(data);
    },
    onMutate: async ({ sid, is_included }) => {
      await qc.cancelQueries({ queryKey: key });
      const previous = qc.getQueryData<SourceListResponse>(key);
      if (previous) {
        qc.setQueryData<SourceListResponse>(key, {
          ...previous,
          items: previous.items.map((s) => (s.id === sid ? { ...s, is_included } : s)),
        });
      }
      return { previous };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) qc.setQueryData(key, ctx.previous);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: key });
    },
  });
}

export function useUploadSource(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (file: File): Promise<Source> => {
      const fd = new FormData();
      fd.append("file", file);
      const data = await apiClient.post<unknown>(`projects/${projectId}/sources/upload`, {
        body: fd,
      });
      return SourceSchema.parse(data);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: sourceKeys.list(projectId) });
    },
  });
}

export function useFinalizeSources(projectId: string) {
  return useMutation({
    mutationFn: async () => {
      await apiClient.post<unknown>(`projects/${projectId}/sources/finalize`);
    },
  });
}
