import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";
import type { Source, SourceListResponse } from "@/api/types";

// ─── 실계약: /projects/{id}/sources ──────────────────────────────────────
// project_sources 행 + metadata 신호(웹 수집은 indexer가, 파일 소스는 업로드/불러오기가 영속화).
// is_included는 2-상태(bool) — 기본 채택, 사람은 제외만 고른다.
// 자료 추가 3경로: 인터넷 검색(수집) · 직접 업로드(POST /sources/upload) ·
//   라이브러리 불러오기(POST /sources/library). 확정은 진행 게이트의 POST /decide.
export const SourceItemSchema = z.object({
  id: z.string(),
  source_type: z.enum(["library", "upload", "web_search"]),
  title: z.string().nullish(),
  url: z.string().nullish(),
  reliability: z.enum(["high", "medium", "low"]).nullish(),
  is_included: z.boolean(),
  matched_sections: z.array(z.string()),
  page_age: z.string().nullish(),
  preview: z.string().nullish(),
  has_content: z.boolean(),
  created_at: z.string(),
});
export type SourceItem = z.infer<typeof SourceItemSchema>;

// 기존 화면(SourceCard·필터·상세)이 쓰는 레거시 Source 모양으로 변환.
// 신뢰도는 high/medium/low → 대표 수치 근사(표시용).
const RELIABILITY_NUM: Record<string, number> = { high: 0.9, medium: 0.6, low: 0.3 };

function toLegacySource(projectId: string, s: SourceItem): Source {
  let host = "";
  if (s.url) {
    try {
      host = new URL(s.url).hostname;
    } catch {
      host = s.url;
    }
  }
  return {
    id: s.id,
    project_id: projectId,
    title: s.title ?? s.url ?? "(제목 없음)",
    source: host || (s.source_type === "web_search" ? "웹 검색" : s.source_type),
    source_kind: s.source_type,
    url: s.url ?? undefined,
    published_at: s.page_age ?? undefined,
    reliability: s.reliability ? (RELIABILITY_NUM[s.reliability] ?? 0.5) : 0.5,
    summary: s.preview ?? (s.has_content ? "" : "본문 회수 실패 - 검색 근거로 쓰이지 않습니다."),
    is_included: s.is_included,
    preview: s.preview ?? undefined,
    matched_sections: s.matched_sections,
  };
}

export const sourceKeys = {
  all: ["sources"] as const,
  list: (projectId: string) => [...sourceKeys.all, "list", projectId] as const,
};

export async function getProjectSources(projectId: string): Promise<SourceListResponse> {
  const data = await apiClient.get<unknown>(`projects/${projectId}/sources`);
  const items = z.array(SourceItemSchema).parse(data);
  return { items: items.map((s) => toLegacySource(projectId, s)), total: items.length };
}

export function useProjectSources(projectId: string, opts?: { refetchInterval?: number | false }) {
  return useQuery({
    queryKey: sourceKeys.list(projectId),
    queryFn: () => getProjectSources(projectId),
    enabled: Boolean(projectId),
    // 추가 검색이 백그라운드에서 도는 동안 목록을 폴링으로 따라잡는 용도.
    refetchInterval: opts?.refetchInterval ?? false,
  });
}

export interface PatchSourceInput {
  sid: string;
  is_included: boolean;
}

export function usePatchSource(projectId: string) {
  const qc = useQueryClient();
  const key = sourceKeys.list(projectId);
  return useMutation({
    mutationFn: async ({ sid, is_included }: PatchSourceInput) => {
      const data = await apiClient.patch<unknown>(`projects/${projectId}/sources/${sid}`, {
        json: { is_included },
      });
      return toLegacySource(projectId, SourceItemSchema.parse(data));
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

// 직접 업로드 자료 — 파일 저장 + 즉시 색인(백엔드). 성공 시 자료 목록 갱신.
export function useUploadProjectSource(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      const data = await apiClient.post<unknown>(`projects/${projectId}/sources/upload`, {
        body: fd,
      });
      return toLegacySource(projectId, SourceItemSchema.parse(data));
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: sourceKeys.list(projectId) });
    },
  });
}

// 라이브러리 파일 불러오기 — 원본 파일을 색인해 근거로 편입. 성공 시 자료 목록 갱신.
export function useAttachLibrarySource(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (libraryNodeId: string) => {
      const data = await apiClient.post<unknown>(`projects/${projectId}/sources/library`, {
        json: { library_node_id: libraryNodeId },
      });
      return toLegacySource(projectId, SourceItemSchema.parse(data));
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: sourceKeys.list(projectId) });
    },
  });
}
