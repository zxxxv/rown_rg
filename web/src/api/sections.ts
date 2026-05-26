import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/api/client";
import {
  type SectionContentResponse,
  SectionContentResponseSchema,
  type SectionTreeResponse,
  SectionTreeResponseSchema,
  type Source,
  SourceSchema,
} from "@/api/types";

export const sectionKeys = {
  all: ["sections"] as const,
  tree: (projectId: string) => [...sectionKeys.all, "tree", projectId] as const,
  content: (projectId: string, sectionId: string) =>
    [...sectionKeys.all, "content", projectId, sectionId] as const,
};

export const sourceRefKeys = {
  detail: (srcId: string) => ["source-ref", srcId] as const,
};

export async function getProjectSections(projectId: string): Promise<SectionTreeResponse> {
  const data = await apiClient.get<unknown>(`projects/${projectId}/sections`);
  return SectionTreeResponseSchema.parse(data);
}

export function useProjectSections(projectId: string) {
  return useQuery({
    queryKey: sectionKeys.tree(projectId),
    queryFn: () => getProjectSections(projectId),
    enabled: Boolean(projectId),
  });
}

export async function getSectionContent(
  projectId: string,
  sectionId: string,
): Promise<SectionContentResponse> {
  const data = await apiClient.get<unknown>(`projects/${projectId}/sections/${sectionId}`);
  return SectionContentResponseSchema.parse(data);
}

export function useSectionContent(projectId: string, sectionId: string | null) {
  return useQuery({
    queryKey: sectionKeys.content(projectId, sectionId ?? ""),
    queryFn: () => {
      if (!sectionId) throw new Error("sectionId required");
      return getSectionContent(projectId, sectionId);
    },
    enabled: Boolean(projectId && sectionId),
    retry: false,
  });
}

export async function getSourceRef(srcId: string): Promise<Source> {
  const data = await apiClient.get<unknown>(`sources/${srcId}`);
  return SourceSchema.parse(data);
}

export function useSourceRef(srcId: string | null) {
  return useQuery({
    queryKey: sourceRefKeys.detail(srcId ?? ""),
    queryFn: () => {
      if (!srcId) throw new Error("srcId required");
      return getSourceRef(srcId);
    },
    enabled: Boolean(srcId),
    staleTime: Number.POSITIVE_INFINITY,
  });
}
