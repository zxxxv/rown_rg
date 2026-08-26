import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/api/client";
import {
  type SectionContentResponse,
  SectionContentResponseSchema,
  type SectionEvidence,
  SectionEvidenceSchema,
  type SectionTreeResponse,
  SectionTreeResponseSchema,
  type Source,
  type SourceDocument,
  SourceDocumentSchema,
  SourceSchema,
} from "@/api/types";
import { verifyKeys } from "@/api/verify";

export const sectionKeys = {
  all: ["sections"] as const,
  tree: (projectId: string) => [...sectionKeys.all, "tree", projectId] as const,
  content: (projectId: string, sectionId: string) =>
    [...sectionKeys.all, "content", projectId, sectionId] as const,
  evidence: (projectId: string, sectionId: string) =>
    [...sectionKeys.all, "evidence", projectId, sectionId] as const,
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

// ─── 근거 추적 ──────────────────────────────────────────────────────────────
// 출처 표기는 "이 자료 어딘가"까지만 알려준다. 이 조회는 문장이 나온 청크 원문과
// 프롬프트에 실렸지만 인용되지 않은 근거까지 함께 돌려줘 창작 여부를 대조하게 한다.

export async function getSectionEvidence(
  projectId: string,
  sectionId: string,
): Promise<SectionEvidence> {
  const data = await apiClient.get<unknown>(`projects/${projectId}/sections/${sectionId}/evidence`);
  return SectionEvidenceSchema.parse(data);
}

export function useSectionEvidence(projectId: string, sectionId: string | null, enabled = true) {
  return useQuery({
    queryKey: sectionKeys.evidence(projectId, sectionId ?? ""),
    queryFn: () => {
      if (!sectionId) throw new Error("sectionId required");
      return getSectionEvidence(projectId, sectionId);
    },
    enabled: Boolean(projectId && sectionId) && enabled,
    retry: false,
  });
}

// ─── 부분 편집: 수동 저장(PATCH) · AI 재작성(POST) ──────────────────────────

export async function invalidateSection(
  qc: ReturnType<typeof useQueryClient>,
  projectId: string,
  sectionId: string,
) {
  await Promise.all([
    qc.invalidateQueries({ queryKey: sectionKeys.content(projectId, sectionId) }),
    qc.invalidateQueries({ queryKey: sectionKeys.tree(projectId) }),
    // 본문이 바뀌면 인용 마커와 무인용 주장 수가 함께 달라진다.
    qc.invalidateQueries({ queryKey: sectionKeys.evidence(projectId, sectionId) }),
    // PM 검증 상태도 다시 읽는다 - 본문이 바뀌면 남아 있는 경고가 낡는데(stale),
    // 이걸 안 무효화하면 캐시가 "안 낡았다"를 계속 돌려줘 배지가 **새로고침 전엔
    // 안 뜬다**(2026-08-27 사용자 지적: "그런 말이 없어").
    qc.invalidateQueries({ queryKey: verifyKeys.all }),
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

/** 절 잠금 토글 - 켜면 AI가 이 절을 못 건드린다(사람의 직접 편집은 그대로).
 *
 *  묶음 재작성은 한 번에 수십 절을 갈아엎는다(전체 실측 $15.5). 공들여 손본 절이
 *  거기 섞이면 그 손질이 사라진다 - 버전으로 되돌릴 수는 있어도 어느 절이 덮였는지
 *  찾는 건 사람 몫이라, 사고를 일어나기 전에 막는 자물쇠다. */
export function useSetSectionLock(projectId: string, sectionId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...sectionKeys.content(projectId, sectionId), "lock"],
    mutationFn: async (locked: boolean) => {
      const data = await apiClient.patch<unknown>(
        `projects/${projectId}/sections/${sectionId}/lock`,
        { json: { locked } },
      );
      return SectionContentResponseSchema.parse(data);
    },
    onSuccess: () => {
      invalidateSection(qc, projectId, sectionId);
      void qc.invalidateQueries({ queryKey: ["drift", projectId] });
    },
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

// ─── 원문 뷰어 ──────────────────────────────────────────────────────────────
// 자료의 색인 본문 전체를 청크 순서대로 받는다. 근거 카드의 "원문에서 보기"가
// 청크 id·문자 오프셋으로 해당 대목까지 스크롤·강조하는 데 쓴다.

export const sourceDocKeys = {
  detail: (projectId: string, sourceId: string) =>
    ["source-document", projectId, sourceId] as const,
};

export async function getSourceDocument(
  projectId: string,
  sourceId: string,
): Promise<SourceDocument> {
  const data = await apiClient.get<unknown>(`projects/${projectId}/sources/${sourceId}/document`);
  return SourceDocumentSchema.parse(data);
}

export function useSourceDocument(projectId: string, sourceId: string | null) {
  return useQuery({
    queryKey: sourceDocKeys.detail(projectId, sourceId ?? ""),
    queryFn: () => {
      if (!sourceId) throw new Error("sourceId required");
      return getSourceDocument(projectId, sourceId);
    },
    enabled: Boolean(projectId && sourceId),
    // 색인이 다시 돌지 않는 한 문서는 안 변한다 - 패널을 여닫을 때마다 재요청하지 않는다
    staleTime: 5 * 60 * 1000,
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
