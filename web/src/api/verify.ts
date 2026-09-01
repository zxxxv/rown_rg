import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";

// 실계약: GET /projects/{id}/verify-report - PM 검증 경고 리포트(차단 아님).
// assemble 직후 챕터당 1콜 pm_verify가 저장한 문서 횡단 일관성 경고.
// 아직 검증 전이거나 경고가 없으면 빈 배열이 온다.
export const VerifyFindingSchema = z.object({
  id: z.string(),
  chapter_number: z.number().int(),
  severity: z.enum(["critical", "warning"]).catch("warning"),
  category: z.string(),
  section_ref: z.string().nullish(),
  /** 절 안정 id(sections.id) - 절 매칭·이동 정본. ref 문자열은 사람이 읽는 표시값 */
  section_id: z.string().nullish(),
  detail: z.string(),
  created_at: z.string(),
  /** 내용 지문 - 재검증이 행을 갈아치워도 완료 표시가 따라온다(id는 매번 바뀐다) */
  key: z.string().default(""),
  resolved: z.boolean().default(false),
});
export type VerifyFinding = z.infer<typeof VerifyFindingSchema>;

export const VerifyReportSchema = z.array(VerifyFindingSchema);

export const verifyKeys = {
  all: ["verify-report"] as const,
  report: (projectId: string) => [...verifyKeys.all, projectId] as const,
};

export async function getVerifyReport(projectId: string): Promise<VerifyFinding[]> {
  const data = await apiClient.get<unknown>(`projects/${projectId}/verify-report`);
  return VerifyReportSchema.parse(data);
}

export function useVerifyReport(projectId: string, enabled = true, generating = false) {
  const running = useVerifyStatus(projectId, enabled).data?.running ?? false;
  return useQuery({
    queryKey: verifyKeys.report(projectId),
    queryFn: () => getVerifyReport(projectId),
    enabled: Boolean(projectId) && enabled,
    // 두 창을 따라간다: ①수동 재검증(running) ②첫 생성(generating). ②가 없으면
    // 완주 전에 열어 둔 화면이 빈 배열을 한 번 받고 멈춰, 조립 직후 pm_verify가
    // 쓴 경고가 새로고침 전엔 안 보였다(2026-08-20 3차 런에서 사용자 발견 - 89건이
    // DB에 있는데 화면은 0건). 끝나면 스스로 멈추는 건 그대로다.
    refetchInterval: running || generating ? 4000 : false,
  });
}

// ─── 검사 커버리지 ───
// 경고 0건이 '깨끗함'으로 읽히면 안 된다 - 무엇을 검사했고(분모) 무엇을 못 보는지
// 함께 보여야 한다. 값은 서버가 조회 시 계산한다(본문을 고치면 따라온다).
export const VerifyCoverageSchema = z.object({
  n_sections: z.number().int(),
  /** 주장 후보 문장 수(제목·표·캡션 제외) */
  n_candidates: z.number().int(),
  /** 검사망에 들어간 문장 수 */
  n_claims: z.number().int(),
  claim_coverage: z.number().nullable(),
  /** 수치를 실었는데 검사망 밖인 문장 - 0이 아니면 분해 회귀 */
  missed_numeric: z.number().int(),
  llm_verify_enabled: z.boolean(),
  pm_verify_enabled: z.boolean(),
});
export type VerifyCoverage = z.infer<typeof VerifyCoverageSchema>;

export function useVerifyCoverage(projectId: string, enabled = true) {
  return useQuery({
    queryKey: [...verifyKeys.report(projectId), "coverage"],
    queryFn: async () => {
      const data = await apiClient.get<unknown>(`projects/${projectId}/verify-coverage`);
      return VerifyCoverageSchema.parse(data);
    },
    enabled: Boolean(projectId) && enabled,
    staleTime: 60_000,
  });
}

// ─── 재검증 ───
// 검증은 조립 때 한 번만 돌아서, 지적을 고쳐도 경고가 그대로 남아 있었다.
// 챕터당 1콜이라 수십 초~수 분이 걸린다 - 요청 밖에서 돌리고 상태를 폴링한다.

export function useVerifyStatus(projectId: string, enabled = true) {
  return useQuery({
    queryKey: [...verifyKeys.report(projectId), "status"],
    queryFn: async () => {
      const data = await apiClient.get<unknown>(`projects/${projectId}/verify-report/status`);
      return z
        .object({
          running: z.boolean(),
          // 지금 남아 있는 경고가 **지금 본문**에 대한 판정인가. 검증은 조립 때와
          // 사람이 누를 때만 돌아서, 그 사이 절을 고치면 경고가 낡는다(2026-08-27).
          // 옛 백엔드·검증 전에는 필드가 없거나 null - 모르는 것을 낡았다고 하지 않는다.
          stale: z.boolean().catch(false),
          verified_at: z.string().nullish(),
        })
        .parse(data);
    },
    enabled: Boolean(projectId) && enabled,
    refetchInterval: (query) => (query.state.data?.running ? 4000 : false),
  });
}

export function useRerunVerify(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...verifyKeys.report(projectId), "rerun"],
    mutationFn: () => apiClient.post<unknown>(`projects/${projectId}/verify-report`, { json: {} }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: verifyKeys.report(projectId) });
    },
  });
}

export function useResolveFinding(projectId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (input: { id: string; resolved: boolean }) => {
      const data = await apiClient.patch<unknown>(
        `projects/${projectId}/verify-report/${input.id}`,
        { json: { resolved: input.resolved } },
      );
      return VerifyFindingSchema.parse(data);
    },
    // 체크박스는 즉시 반응해야 한다 - 서버 왕복을 기다리면 눌린 것 같지가 않다.
    onMutate: async ({ id, resolved }) => {
      const key = verifyKeys.report(projectId);
      await qc.cancelQueries({ queryKey: key });
      const previous = qc.getQueryData<VerifyFinding[]>(key);
      if (previous) {
        qc.setQueryData<VerifyFinding[]>(
          key,
          previous.map((f) => (f.id === id ? { ...f, resolved } : f)),
        );
      }
      return { previous };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.previous) qc.setQueryData(verifyKeys.report(projectId), ctx.previous);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: verifyKeys.report(projectId) });
    },
  });
}
