import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";
import { sectionKeys } from "@/api/sections";

// 실계약: GET /projects/{id}/drift - 설계 변경이 아직 본문에 닿지 않은 절("미반영").
// 보고서는 완성 순간이 끝이 아니다(2026-08-25) - 완료 뒤에도 목차를 고치거나 자료를
// 빼면, 그 변경이 본문에 반영되기 전까지 여기에 뜬다. 본문이 틀렸다는 뜻이 아니라
// '그때 계약대로 쓰였고 그 뒤 계약이 바뀌었다'는 뜻이다.
// 서버는 판정만 한다 - 재작성은 사람이 고른다(절당 실측 $0.67).

export const DRIFT_REASON_LABEL: Record<string, string> = {
  plan_changed: "목차 수정 미반영",
  source_excluded: "자료 제외 미반영",
  missing: "본문 없음",
};

export const DriftSectionSchema = z.object({
  section_id: z.string(),
  /** "2.3 인구·고령화 영향" - 사람이 읽는 좌표 */
  label: z.string(),
  reasons: z.array(z.string()).default([]),
  excluded_sources: z.array(z.object({ id: z.string(), title: z.string() })).default([]),
  /** 잠긴 절 - 미반영이어도 다시 쓸 수 없다. 사실은 사실이고, 고를 수만 없다. */
  locked: z.boolean().default(false),
});
export type DriftSection = z.infer<typeof DriftSectionSchema>;

export const DriftRelatedSchema = z.object({
  section_id: z.string(),
  label: z.string(),
  /** 어느 미반영 절을 이어받는가 - "왜 여기 떴는지"의 이유 */
  via: z.array(z.string()).default([]),
  locked: z.boolean().default(false),
});
export type DriftRelated = z.infer<typeof DriftRelatedSchema>;

export const DriftSchema = z.object({
  sections: z.array(DriftSectionSchema).default([]),
  /** 미반영 절을 이어받는(builds_on) 절 - 미반영은 아니지만 전제가 바뀔 수 있어
   *  후보로만 보인다. 기본 미선택 - 고르는 것은 사람 몫(2026-08-28). */
  related: z.array(DriftRelatedSchema).default([]),
  n_plan_changed: z.number().int().default(0),
  n_source_excluded: z.number().int().default(0),
  n_missing: z.number().int().default(0),
});
export type Drift = z.infer<typeof DriftSchema>;

export const driftKeys = {
  all: ["drift"] as const,
  detail: (projectId: string) => [...driftKeys.all, projectId] as const,
};

export function useDrift(projectId: string, enabled = true) {
  return useQuery({
    queryKey: driftKeys.detail(projectId),
    queryFn: async () => {
      const data = await apiClient.get<unknown>(`projects/${projectId}/drift`);
      return DriftSchema.parse(data);
    },
    enabled: Boolean(projectId) && enabled,
    staleTime: 15_000,
  });
}

// ─── 묶음 재작성 ───
// 미반영 절을 골라 다시 쓴다. 절당 실측 $0.67·수십 초라 요청 밖에서 돌리고 진행을
// 폴링한다. 자동으로 걸지 않는 이유도 그 값이다 - 대상은 사람이 고른다.

export const RewriteBatchStatusSchema = z.object({
  running: z.boolean().default(false),
  total: z.number().int().default(0),
  done: z.number().int().default(0),
  /** 지금(또는 마지막으로) 처리한 절 라벨 - 진행 문구에 쓴다 */
  current: z.string().default(""),
  /** 대상별 실패 사유 - 부분 실패를 삼키지 않는다 */
  failures: z.record(z.string(), z.string()).default({}),
  /** 멈춤 요청이 들어왔는가 - 돌던 절 하나는 마치고 다음으로 넘어가지 않는다 */
  cancelled: z.boolean().default(false),
});
export type RewriteBatchStatus = z.infer<typeof RewriteBatchStatusSchema>;

export function useRewriteBatchStatus(projectId: string, enabled = true) {
  return useQuery({
    queryKey: [...driftKeys.detail(projectId), "rewrite-batch"],
    queryFn: async () => {
      const data = await apiClient.get<unknown>(`projects/${projectId}/rewrite-batch`);
      return RewriteBatchStatusSchema.parse(data);
    },
    enabled: Boolean(projectId) && enabled,
    refetchInterval: (query) => (query.state.data?.running ? 3000 : false),
  });
}

export function useRewriteBatch(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...driftKeys.detail(projectId), "rewrite-batch", "start"],
    mutationFn: (input: { sectionIds: string[]; instruction?: string }) =>
      apiClient.post<unknown>(`projects/${projectId}/rewrite-batch`, {
        json: { section_ids: input.sectionIds, instruction: input.instruction ?? "" },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: driftKeys.detail(projectId) });
    },
  });
}

/** 묶음 멈춤 - 돌던 절 하나는 끝까지 마치고 다음으로 넘어가지 않는다(반쪽 저장 방지). */
export function useCancelRewriteBatch(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...driftKeys.detail(projectId), "rewrite-batch", "cancel"],
    mutationFn: () => apiClient.delete<unknown>(`projects/${projectId}/rewrite-batch`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: driftKeys.detail(projectId) });
    },
  });
}

/** 미반영 무시 - "이 절은 이대로 둔다"는 선언. 본문은 건드리지 않는다.
 *
 *  서버는 지금 계획의 지문을 그대로 찍는다 - 다음에 계획이 또 바뀌면 저절로 다시 뜬다.
 *  영구 침묵이 아니라 지금 것만 넘기는 것이다. 본문 없는 절은 무시할 수 없다(skipped). */
export function useDismissDrift(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...driftKeys.detail(projectId), "dismiss"],
    mutationFn: (sectionIds: string[]) =>
      apiClient.post<{ dismissed: string[]; skipped: string[] }>(
        `projects/${projectId}/drift/dismiss`,
        { json: { section_ids: sectionIds } },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: driftKeys.detail(projectId) });
    },
  });
}

/** 묶음이 끝나면 미반영 목록과 본문 트리를 함께 새로 읽는다 - 배지가 내려가야 한다. */
export function invalidateAfterRewrite(qc: ReturnType<typeof useQueryClient>, projectId: string) {
  void qc.invalidateQueries({ queryKey: driftKeys.detail(projectId) });
  void qc.invalidateQueries({ queryKey: sectionKeys.tree(projectId) });
}
