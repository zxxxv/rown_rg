import type { QuotaSetting } from "@/api/quota-settings";

// 백엔드 시딩값과 동일 (core.quota_settings.QUOTA_SETTING_SEED_VALUES).
const SEED: Record<string, string> = {
  ORG_MONTHLY_COST_LIMIT_USD: "3000",
  DEFAULT_LIMIT_SUPER_ADMIN_USD: "500",
  DEFAULT_LIMIT_ADMIN_USD: "300",
  DEFAULT_LIMIT_WORKER_USD: "200",
  DEFAULT_LIMIT_VIEWER_USD: "50",
};

// 인메모리 상태 — mock 세션 동안 PATCH가 누적 반영된다.
const rows: QuotaSetting[] = Object.entries(SEED).map(([key, value]) => ({
  key,
  value,
  updated_at: "2026-08-01T00:00:00Z",
  updated_by: null,
}));

export function listQuotaSettings(): QuotaSetting[] {
  return rows.map((r) => ({ ...r }));
}

export function applyQuotaPatch(patch: Record<string, string>): QuotaSetting[] {
  const now = new Date().toISOString();
  for (const [key, value] of Object.entries(patch)) {
    const row = rows.find((r) => r.key === key);
    if (row) {
      row.value = value;
      row.updated_at = now;
      row.updated_by = "mock-super-admin";
    }
  }
  return listQuotaSettings();
}
