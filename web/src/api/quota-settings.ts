import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";

// 백엔드 quota_settings 한 행 - GET/PATCH 모두 이 배열을 그대로 반환한다.
export const QuotaSettingSchema = z.object({
  key: z.string(),
  value: z.string(),
  updated_at: z.string(),
  updated_by: z.string().nullable(),
});
export type QuotaSetting = z.infer<typeof QuotaSettingSchema>;

// 화이트리스트 key → 라벨·검증 범위 (백엔드 core.quota_settings.QUOTA_SETTING_RULES와 일치).
// 조직 상한은 1~1,000,000, 역할 기본은 1~100,000.
export const QUOTA_META = [
  {
    key: "ORG_MONTHLY_COST_LIMIT_USD",
    label: "조직 전체 월 한도",
    hint: "회사 전체 월 비용 천장 - 초과 시 전원 차단",
    min: 1,
    max: 1_000_000,
  },
  {
    key: "DEFAULT_LIMIT_SUPER_ADMIN_USD",
    label: "super_admin 기본 한도",
    hint: "개인 지정이 없는 super_admin에게 적용",
    min: 1,
    max: 100_000,
  },
  {
    key: "DEFAULT_LIMIT_ADMIN_USD",
    label: "admin 기본 한도",
    hint: "개인 지정이 없는 admin에게 적용",
    min: 1,
    max: 100_000,
  },
  {
    key: "DEFAULT_LIMIT_WORKER_USD",
    label: "worker 기본 한도",
    hint: "개인 지정이 없는 worker(신규 포함)에게 적용",
    min: 1,
    max: 100_000,
  },
  // viewer 한도 설정란은 없음 - 뷰어는 읽기 전용이라 기본 $0 고정(2026-08-15 결정).
  // LLM 호출 경로 자체가 없어 조정할 이유가 없고, 조정이 필요해지면 그때 역할 설계부터
  // 다시 본다(백엔드 core.limit DEFAULT_ROLE_LIMIT_USD.viewer = 0).
] as const;

export const quotaSettingsKeys = {
  all: ["quota-settings"] as const,
};

export async function getQuotaSettings(): Promise<QuotaSetting[]> {
  const data = await apiClient.get<unknown>("admin/quota-settings");
  return z.array(QuotaSettingSchema).parse(data);
}

export function useQuotaSettings() {
  return useQuery({ queryKey: quotaSettingsKeys.all, queryFn: getQuotaSettings });
}

// 배치 수정 - {key: value} 딕셔너리. 백엔드가 검증·감사 후 전체 최신본을 돌려준다.
export async function updateQuotaSettings(patch: Record<string, string>): Promise<QuotaSetting[]> {
  const data = await apiClient.patch<unknown>("admin/quota-settings", { json: patch });
  return z.array(QuotaSettingSchema).parse(data);
}

export function useUpdateQuotaSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...quotaSettingsKeys.all, "update"],
    mutationFn: updateQuotaSettings,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: quotaSettingsKeys.all });
    },
  });
}
