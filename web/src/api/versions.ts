import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";
import { projectKeys } from "@/api/projects";

// 실계약: /projects/{id}/versions* - 보고서 버전 스냅샷(append-only)과 절 단위 비교.
// 스냅샷은 조립 완성·재개 직전에 자동으로 쌓이고, diff는 절 안정 id로 서버가 판정한다.

export const ReportVersionSchema = z.object({
  version_no: z.number().int(),
  reason: z.enum(["assemble", "reopen", "manual"]).catch("manual"),
  created_at: z.string(),
  n_sections: z.number().int(),
  total_chars: z.number().int(),
});
export type ReportVersion = z.infer<typeof ReportVersionSchema>;

export const VersionSectionSchema = z.object({
  section_id: z.string(),
  chapter_number: z.number().int(),
  section_number: z.number().int(),
  chapter_title: z.string().catch(""),
  title: z.string(),
  content: z.string(),
});
export type VersionSection = z.infer<typeof VersionSectionSchema>;

export const VersionDiffEntrySchema = z.object({
  section_id: z.string(),
  status: z.enum(["added", "removed", "modified", "unchanged"]).catch("unchanged"),
  moved: z.boolean().catch(false),
  base: VersionSectionSchema.nullish(),
  target: VersionSectionSchema.nullish(),
});
export type VersionDiffEntry = z.infer<typeof VersionDiffEntrySchema>;

export const VersionDiffSchema = z.object({
  base_version: z.number().int(),
  target_version: z.number().int().nullish(),
  n_added: z.number().int(),
  n_removed: z.number().int(),
  n_modified: z.number().int(),
  n_unchanged: z.number().int(),
  entries: z.array(VersionDiffEntrySchema),
});
export type VersionDiff = z.infer<typeof VersionDiffSchema>;

export const versionKeys = {
  all: ["report-versions"] as const,
  list: (projectId: string) => [...versionKeys.all, projectId] as const,
  diff: (projectId: string, base: number, target: number | null) =>
    [...versionKeys.all, projectId, "diff", base, target] as const,
};

export function useReportVersions(projectId: string, enabled = true) {
  return useQuery({
    queryKey: versionKeys.list(projectId),
    queryFn: async () => {
      const data = await apiClient.get<unknown>(`projects/${projectId}/versions`);
      return z.array(ReportVersionSchema).parse(data);
    },
    enabled: Boolean(projectId) && enabled,
    staleTime: 30_000,
  });
}

/** target=null이면 현재 작업 사본과 비교(가장 흔한 용례 - "그때와 지금"). */
export function useVersionDiff(projectId: string, base: number | null, target: number | null) {
  return useQuery({
    queryKey: versionKeys.diff(projectId, base ?? 0, target),
    queryFn: async () => {
      const params = new URLSearchParams({ base: String(base) });
      if (target !== null) params.set("target", String(target));
      const data = await apiClient.get<unknown>(
        `projects/${projectId}/versions/diff?${params.toString()}`,
      );
      return VersionDiffSchema.parse(data);
    },
    enabled: Boolean(projectId) && base !== null,
    staleTime: 30_000,
  });
}

/** 재개 - 현재 완성본을 버전으로 얼리고 자료 단계로 되돌린다(실행은 사람이 시작). */
export function useReopenProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (projectId: string) =>
      apiClient.post<unknown>(`projects/${projectId}/reopen`, { json: {} }),
    onSuccess: (_data, projectId) => {
      void qc.invalidateQueries({ queryKey: projectKeys.detail(projectId) });
      void qc.invalidateQueries({ queryKey: versionKeys.list(projectId) });
    },
  });
}
