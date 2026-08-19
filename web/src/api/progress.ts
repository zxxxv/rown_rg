import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { z } from "zod";
import { apiClient } from "@/api/client";
import { type ProjectStatus, ProjectStatusSchema } from "@/api/types";
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
  // 백엔드 ProjectStage. 프로젝트 목록·개요가 쓰는 ProjectStatus와 같은 값이라 같은
  // enum으로 받는다 - string으로 두면 주 CTA·단계 카드가 이 값을 못 쓴다(타입 불일치).
  // 모르는 값(백엔드가 먼저 배포된 경우)은 catch로 흘려 화면이 죽지 않게 한다.
  status: ProjectStatusSchema.catch("created"),
  pending_gate: PendingGateSchema.nullable(),
  // 단계 기반 근사 진행률(0~100) + 프로젝트 누적 토큰·비용 (구백엔드 호환 위해 default)
  percent: z.number().min(0).max(100).default(0),
  tokens_used: z.number().nonnegative().default(0),
  cost_usd: z.number().nonnegative().default(0),
  // 실행 시작·마지막 활동(ISO) - token_usage 첫/마지막 기록 근사. 기록 없으면 null.
  started_at: z.string().nullish(),
  last_activity_at: z.string().nullish(),
  // 전역 동시 실행 상한 대기열 위치(1부터) - 대기 중이 아니면 null
  queue_position: z.number().int().positive().nullish(),
  // 실행 중 세부 단계 라벨(예: "청킹·임베딩 5/17", "배경 요약 1층 · 40/152") -
  // 색인·RAPTOR 같은 수 분짜리 단계의 내부 진행. 없으면 null.
  active_step: z.string().nullish(),
  active_steps: z.array(z.string()).nullish(),
  active_seconds: z.number().nonnegative().nullish(),
  source_target: z.number().nullish(),
  // 백엔드 프로세스에 살아 있는 러너 태스크가 있는지 - 구백엔드 호환 위해 default true
  runner_alive: z.boolean().default(true),
  // 마지막 진행 이벤트 시각(ISO) - 서버 재시작 후엔 null
  last_event_at: z.string().nullish(),
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
  active_steps?: string[];
  /** 순수 생성 시간(초) - 검토 대기·중단을 뺀 실제 작업 시간 */
  active_seconds?: number;
  /** 자료 수집 목표 건수(권장 하한) - 수집 중 '현재 n/목표 m' 표시용 */
  source_target?: number;
  /** 단계 기반 근사 전체 진행률(0~100) */
  percent: number;
  tokens_used?: number;
  cost_usd?: number;
  eta_seconds?: number;
  pending_checkpoint_id: string | null;
  pending_gate: PendingGate | null;
  /** status=created - 아직 실행이 시작되지 않음(자료조사 진행처럼 그리면 안 됨) */
  not_started: boolean;
  /** status=cancelled - 사용자가 실행 도중 취소함 */
  cancelled: boolean;
  /** 원본 status(취소 등 판정용). 개요의 주 CTA·단계 카드도 이 값을 쓴다 - 프로젝트
   * 상세 스냅샷은 /run 뒤 한동안 낡아 있어서 서버가 진실인 이 값을 우선한다. */
  status: ProjectStatus;
  /** 실행 시작 시각(ISO) - 페이지 재진입에도 경과 시간이 이어지도록 서버 값 사용 */
  started_at: string | null;
  /** 마지막 활동 시각(ISO) - 종료된 프로젝트의 경과 시간 고정용 */
  last_activity_at: string | null;
  /** 실행 대기열 위치(1부터) - 동시 실행 상한 초과로 대기 중일 때만 값 존재 */
  queue_position: number | null;
  /**
   * 죽은 런 - 작업 단계인데 게이트 대기도 아니고 백엔드에 살아 있는 태스크도 없음.
   * 서버 재시작·오류로 실행이 끊긴 상태라, 스피너 대신 재개 안내를 보여줘야 한다.
   */
  stalled: boolean;
}

const PHASE_ORDER: PhaseName[] = ["research", "indexing", "writing", "qa", "export"];

/** 러너 태스크가 살아 있어야 정상인 작업 단계 - 게이트 대기는 pending_gate로 구분 */
const ACTIVE_WORK_STATUSES = ["planning", "researching", "indexing", "writing", "reviewing"];

const STATUS_TO_PHASE: Record<string, PhaseName> = {
  created: "research",
  planning: "research",
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
    active_steps: res.active_steps ?? undefined,
    active_seconds: res.active_seconds ?? undefined,
    source_target: res.source_target ?? undefined,
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
    stalled: ACTIVE_WORK_STATUSES.includes(res.status) && !res.pending_gate && !res.runner_alive,
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

/**
 * 죽은 런 확정 판정 - 연속 2회 폴링에서 stalled여야 true.
 *
 * 게이트 승인 직후 서버가 결정 반영과 태스크 기동 사이에 있으면 한 번 stalled로
 * 보일 수 있다(찰나의 레이스). 한 번 더 확인해 배너가 깜빡이지 않게 한다.
 */
export function useConfirmedStalled(snapshot: ProgressSnapshot | undefined): boolean {
  const countRef = useRef(0);
  const [confirmed, setConfirmed] = useState(false);
  useEffect(() => {
    if (!snapshot) return;
    countRef.current = snapshot.stalled ? countRef.current + 1 : 0;
    setConfirmed(countRef.current >= 2);
  }, [snapshot]);
  return confirmed;
}
