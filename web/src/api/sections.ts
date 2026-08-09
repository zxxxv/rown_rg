import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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

export function useProjectSections(projectId: string, refetchInterval?: number) {
  return useQuery({
    queryKey: sectionKeys.tree(projectId),
    queryFn: () => getProjectSections(projectId),
    enabled: Boolean(projectId),
    // 작성 진행 중 증분 표시 - 완성된 절이 트리에 순차 반영되도록 폴링
    refetchInterval,
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

// ─── 부분 편집: 수동 저장(PATCH) · AI 재작성(POST) ──────────────────────────

async function invalidateSection(
  qc: ReturnType<typeof useQueryClient>,
  projectId: string,
  sectionId: string,
) {
  await Promise.all([
    qc.invalidateQueries({ queryKey: sectionKeys.content(projectId, sectionId) }),
    qc.invalidateQueries({ queryKey: sectionKeys.tree(projectId) }),
  ]);
}

export async function saveSectionContent(
  projectId: string,
  sectionId: string,
  content: string,
): Promise<SectionContentResponse> {
  const data = await apiClient.patch<unknown>(`projects/${projectId}/sections/${sectionId}`, {
    json: { content },
  });
  return SectionContentResponseSchema.parse(data);
}

export function useSaveSection(projectId: string, sectionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...sectionKeys.content(projectId, sectionId), "save"],
    mutationFn: (content: string) => saveSectionContent(projectId, sectionId, content),
    onSuccess: () => invalidateSection(qc, projectId, sectionId),
  });
}

export async function rewriteSection(
  projectId: string,
  sectionId: string,
  instruction: string,
): Promise<SectionContentResponse> {
  const data = await apiClient.post<unknown>(
    `projects/${projectId}/sections/${sectionId}/rewrite`,
    { json: { instruction } },
  );
  return SectionContentResponseSchema.parse(data);
}

export function useRewriteSection(projectId: string, sectionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...sectionKeys.content(projectId, sectionId), "rewrite"],
    mutationFn: (instruction: string) => rewriteSection(projectId, sectionId, instruction),
    onSuccess: () => invalidateSection(qc, projectId, sectionId),
  });
}

/** 블록 국소 재작성 - 검색 없이 지정 블록만 고쳐 치환 저장(기존 인용 범위 유지). */
export async function rewriteSectionBlock(
  projectId: string,
  sectionId: string,
  block: string,
  instruction: string,
): Promise<SectionContentResponse> {
  const data = await apiClient.post<unknown>(
    `projects/${projectId}/sections/${sectionId}/rewrite-block`,
    { json: { block, instruction } },
  );
  return SectionContentResponseSchema.parse(data);
}

export function useRewriteBlock(projectId: string, sectionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...sectionKeys.content(projectId, sectionId), "rewrite-block"],
    mutationFn: (input: { block: string; instruction: string }) =>
      rewriteSectionBlock(projectId, sectionId, input.block, input.instruction),
    onSuccess: () => invalidateSection(qc, projectId, sectionId),
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
