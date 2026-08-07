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
  // 실행 시작·마지막 활동(ISO) — token_usage 첫/마지막 기록 근사. 기록 없으면 null.
  started_at: z.string().nullish(),
  last_activity_at: z.string().nullish(),
  // 전역 동시 실행 상한 대기열 위치(1부터) — 대기 중이 아니면 null
  queue_position: z.number().int().positive().nullish(),
  // 실행 중 세부 단계 라벨(예: "청킹·임베딩 5/17", "배경 요약 1층 · 40/152") —
  // 색인·RAPTOR 같은 수 분짜리 단계의 내부 진행. 없으면 null.
  active_step: z.string().nullish(),
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
  /** status=created — 아직 실행이 시작되지 않음(자료조사 진행처럼 그리면 안 됨) */
  not_started: boolean;
  /** status=cancelled — 사용자가 실행 도중 취소함 */
  cancelled: boolean;
  /** 원본 status(취소 등 판정용) */
  status: string;
  /** 실행 시작 시각(ISO) — 페이지 재진입에도 경과 시간이 이어지도록 서버 값 사용 */
  started_at: string | null;
  /** 마지막 활동 시각(ISO) — 종료된 프로젝트의 경과 시간 고정용 */
  last_activity_at: string | null;
  /** 실행 대기열 위치(1부터) — 동시 실행 상한 초과로 대기 중일 때만 값 존재 */
  queue_position: number | null;
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
    active_step: res.active_step ?? undefined,
    percent: res.percent,
    tokens_used: res.tokens_used,
    cost_usd: res.cost_usd,
    pending_checkpoint_id: res.pending_gate?.review_point_id ?? null,
    pending_gate: res.pending_gate,
    not_started: res.status === "created",
    cancelled: res.status === "cancelled",
    status: res.status,
    started_at: res.started_at ?? null,
    last_activity_at: res.last_activity_at ?? null,
    queue_position: res.queue_position ?? null,
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

export function useProgressSnapshot(
  projectId: string,
  enabled = true,
  opts?: { refetchInterval?: number | false },
) {
  return useQuery({
    queryKey: progressKeys.snapshot(projectId),
    queryFn: () => getProgressSnapshot(projectId),
    enabled: enabled && Boolean(projectId),
    staleTime: 0,
    // 개요 스테퍼 등 실행 중 화면이 폴링으로 단계 전이를 따라잡는 용도
    refetchInterval: opts?.refetchInterval ?? false,
  });
}
