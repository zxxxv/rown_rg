import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";
import type { PhaseName } from "@/api/ws-messages";

// ─── 실계약: GET /projects/{id}/progress ────────────────────────────────
// 응답 = {project_id, status(백엔드 ProjectStage), pending_gate|null}

export const PendingGateSchema = z.object({
  review_point_id: z.string(),
  gate: z.string(), // source_pool | contradiction | level_1 | level_2 | qa_select | final
  payload: z.record(z.string(), z.unknown()),
});
export type PendingGate = z.infer<typeof PendingGateSchema>;

export const ProjectProgressSchema = z.object({
  project_id: z.string(),
  status: z.string(), // created | researching | indexing | writing | reviewing | completed | archived
  pending_gate: PendingGateSchema.nullable(),
  // 단계 기반 근사 진행률(0~100) + 프로젝트 누적 토큰·비용 (구백엔드 호환 위해 default)
  percent: z.number().min(0).max(100).default(0),
  tokens_used: z.number().nonnegative().default(0),
  cost_usd: z.number().nonnegative().default(0),
});
export type ProjectProgress = z.infer<typeof ProjectProgressSchema>;

// ─── 기존 소비처(useProgressState)용 스냅샷 어댑터 ──────────────────────
// 백엔드 progress에는 토큰·비용·ETA가 없으므로 optional로 두고, 리듀서가
// 값이 없으면 기존(WS로 수신한) 값을 유지한다.

export interface ProgressSnapshot {
  phase: PhaseName;
  phase_status: "started" | "completed";
  completed_phases: PhaseName[];
  active_step?: string;
  /** 단계 기반 근사 전체 진행률(0~100) */
  percent: number;
  tokens_used?: number;
  cost_usd?: number;
  eta_seconds?: number;
  pending_checkpoint_id: string | null;
  pending_gate: PendingGate | null;
}

const PHASE_ORDER: PhaseName[] = ["research", "indexing", "writing", "qa", "export"];

const STATUS_TO_PHASE: Record<string, PhaseName> = {
  created: "research",
  researching: "research",
  indexing: "indexing",
  writing: "writing",
  reviewing: "qa",
  completed: "export",
  archived: "export",
};

export function toProgressSnapshot(res: ProjectProgress): ProgressSnapshot {
  const phase = STATUS_TO_PHASE[res.status] ?? "research";
  const finished = res.status === "completed" || res.status === "archived";
  const phaseIdx = PHASE_ORDER.indexOf(phase);
  const completed_phases = finished
    ? [...PHASE_ORDER]
    : PHASE_ORDER.slice(0, Math.max(0, phaseIdx));
  return {
    phase,
    phase_status: finished ? "completed" : "started",
    completed_phases,
    percent: res.percent,
    tokens_used: res.tokens_used,
    cost_usd: res.cost_usd,
    pending_checkpoint_id: res.pending_gate?.review_point_id ?? null,
    pending_gate: res.pending_gate,
  };
}

export const progressKeys = {
  all: ["progress"] as const,
  snapshot: (projectId: string) => [...progressKeys.all, "snapshot", projectId] as const,
};

export async function getProjectProgress(projectId: string): Promise<ProjectProgress> {
  const data = await apiClient.get<unknown>(`projects/${projectId}/progress`);
  return ProjectProgressSchema.parse(data);
}

export async function getProgressSnapshot(projectId: string): Promise<ProgressSnapshot> {
  return toProgressSnapshot(await getProjectProgress(projectId));
}

export function useProgressSnapshot(projectId: string, enabled = true) {
  return useQuery({
    queryKey: progressKeys.snapshot(projectId),
    queryFn: () => getProgressSnapshot(projectId),
    enabled: enabled && Boolean(projectId),
    staleTime: 0,
  });
}
