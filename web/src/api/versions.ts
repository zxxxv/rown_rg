import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";
import { projectKeys } from "@/api/projects";
import { sectionKeys } from "@/api/sections";
import { verifyKeys } from "@/api/verify";

// 실계약: /projects/{id}/versions* - 보고서 버전 스냅샷(append-only)과 절 단위 비교.
// 스냅샷은 조립 완성·재개 직전에 자동으로 쌓이고, diff는 절 안정 id로 서버가 판정한다.

export const ReportVersionSchema = z.object({
  version_no: z.number().int(),
  // assemble | reopen | finalize | rewrite:<장.절> | block:<장.절> | manual[:<꼬리표>]
  // - 좌표·꼬리표가 붙는 열린 형태라 enum이 아니라 문자열로 받는다(0045).
  reason: z.string().catch("manual"),
  created_at: z.string(),
  n_sections: z.number().int(),
  total_chars: z.number().int(),
  /** 남긴 사람. 자동 스냅샷(조립·검증)은 없다 */
  created_by_name: z.string().nullish(),
  /** 직전 버전 대비 변화 - 첫 버전은 null(견줄 앞이 없다) */
  delta_chars: z.number().int().nullish(),
  n_changed_sections: z.number().int().nullish(),
  /** 지금 본문이 이 버전과 같은가 - 어느 것도 true가 아니면 마지막 스냅샷 이후 손댄 것 */
  is_current: z.boolean().default(false),
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

// ─── 절 하나의 이력 ───
// 실제 동선은 "이 절, 예전 게 나았는데"다 - 버전 기록에서 시작해 그 절을 찾아가는 게
// 아니라 절에서 이력으로 들어간다. 같은 내용이 이어진 구간은 서버가 접어서 준다.

export const SectionHistoryEntrySchema = z.object({
  /** 이 내용이 처음 나타난 버전 / 같은 내용이 유지된 마지막 버전 */
  version_no: z.number().int(),
  until_version: z.number().int(),
  reason: z.string().catch("manual"),
  created_at: z.string(),
  until_at: z.string(),
  title: z.string(),
  chapter_number: z.number().int(),
  section_number: z.number().int(),
  content: z.string(),
  char_count: z.number().int(),
  is_current: z.boolean().default(false),
});
export type SectionHistoryEntry = z.infer<typeof SectionHistoryEntrySchema>;

const SectionHistorySchema = z.object({
  section_id: z.string(),
  entries: z.array(SectionHistoryEntrySchema),
});

export function useSectionHistory(projectId: string, sectionId: string | null) {
  return useQuery({
    queryKey: [...versionKeys.all, projectId, "section", sectionId],
    queryFn: async () => {
      const data = await apiClient.get<unknown>(
        `projects/${projectId}/sections/${sectionId}/history`,
      );
      return SectionHistorySchema.parse(data);
    },
    enabled: Boolean(projectId) && Boolean(sectionId),
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

const ManualVersionResponseSchema = z.object({
  version_no: z.number().int(),
  created: z.boolean(),
});
export type ManualVersionResponse = z.infer<typeof ManualVersionResponseSchema>;

/** 수동 버전 저장 - "이 상태를 남겨두고 계속 고치겠다"는 체크포인트. */
export function useSaveManualVersion(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (note?: string) => {
      const data = await apiClient.post<unknown>(`projects/${projectId}/versions`, {
        json: note ? { note } : {},
      });
      return ManualVersionResponseSchema.parse(data);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: versionKeys.list(projectId) });
    },
  });
}

/** 납품 확정 선언 - completed는 '사이클 완료'일 뿐, 확정은 사람이 누른다. */
export function useFinalizeProject(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.post<unknown>(`projects/${projectId}/finalize`, { json: {} }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: projectKeys.detail(projectId) });
      void qc.invalidateQueries({ queryKey: versionKeys.list(projectId) });
    },
  });
}

/** 확정 해제 - 본문·버전은 그대로 두고 표식만 내린다(고치려면 재개가 맞는 문). */
export function useUnfinalizeProject(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.delete<unknown>(`projects/${projectId}/finalize`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: projectKeys.detail(projectId) });
    },
  });
}

// ─── 절 단위 되돌리기 ───
// 전체 롤백이 아닌 이유: 보고서는 여러 번에 걸쳐 고쳐진다. 한 절이 마음에 안 들어
// 되돌리려는데 전체를 롤백하면 그 사이 손본 다른 절의 개선까지 사라진다(2026-08-26).

export function useRestoreSection(projectId: string, versionNo: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...versionKeys.list(projectId), "restore", versionNo],
    mutationFn: (sectionId: string) =>
      apiClient.post<unknown>(`projects/${projectId}/versions/${versionNo}/restore/${sectionId}`, {
        json: {},
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: versionKeys.all });
      void qc.invalidateQueries({ queryKey: projectKeys.detail(projectId) });
      // 본문도 다시 읽는다 - 절 이력에서 되돌리면 눈앞의 본문이 바로 바뀌어야 한다
      // (비교 뷰에서만 쓰이던 시절엔 diff만 새로 받으면 됐다).
      void qc.invalidateQueries({ queryKey: sectionKeys.all });
      // 되돌리기도 본문을 바꾼다 - PM 검증 판정이 낡았다는 표시가 따라와야 한다.
      void qc.invalidateQueries({ queryKey: verifyKeys.all });
    },
  });
}
