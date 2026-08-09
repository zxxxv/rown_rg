import { useMutation } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";

// 실계약: POST /projects/{id}/decide - body {decision: dict}
// 서버(runner.resume_run)는 최신 pending review_point를 결정값과 함께 resolved 처리한다.
// - source_pool 계열 게이트: {action: "approve" | ...} 형태의 자유 dict
// - qa_select 게이트: {selections: {sectionId: candidateId}} (runner가 decision["selections"] 소비)

export type CheckpointDecision = "approve" | "request_changes" | "halt" | "deeper";

export interface RunResumeResponse {
  project_id: string;
  status: string;
}

export interface DecideCheckpointInput {
  projectId: string;
  /** 화면 표시용 참조 - 서버는 항상 최신 pending 게이트를 처리하므로 전송하지 않는다. */
  checkpointId?: string;
  decision: CheckpointDecision;
  feedback?: string;
}

async function postDecision(
  projectId: string,
  decision: Record<string, unknown>,
): Promise<RunResumeResponse> {
  return apiClient.post<RunResumeResponse>(`projects/${projectId}/decide`, {
    json: { decision },
  });
}

export async function decideCheckpoint(input: DecideCheckpointInput): Promise<RunResumeResponse> {
  return postDecision(input.projectId, {
    action: input.decision,
    ...(input.feedback ? { feedback: input.feedback } : {}),
  });
}

export function useDecideCheckpoint() {
  return useMutation({
    mutationKey: ["checkpoints", "decide"],
    mutationFn: decideCheckpoint,
  });
}

// ─── qa_select 게이트 payload (백엔드 write_loop.qa_select_payload 미러) ───
// 정적검사(HARD) 통과 후보만 내려오고, warnings는 SOFT 경고(분량·수치 근거 등)다.
// all_excluded=true 섹션은 통과 후보가 0개 - 선택 대상에서 제외된다.

export const QaSelectWarningSchema = z.object({
  check: z.string(),
  detail: z.string().nullable().optional(),
});
export type QaSelectWarning = z.infer<typeof QaSelectWarningSchema>;

export const QaSelectCandidateSchema = z.object({
  candidate_id: z.string(),
  content: z.string(),
  cited_chunk_ids: z.array(z.string()).default([]),
  warnings: z.array(QaSelectWarningSchema).default([]),
});
export type QaSelectCandidate = z.infer<typeof QaSelectCandidateSchema>;

export const QaSelectSectionSchema = z.object({
  section_id: z.string(),
  candidates: z.array(QaSelectCandidateSchema).default([]),
  all_excluded: z.boolean().default(false),
});
export type QaSelectSection = z.infer<typeof QaSelectSectionSchema>;

export const QaSelectPlanEntrySchema = z.object({
  section_id: z.string(),
  chapter_number: z.number(),
  section_number: z.number(),
  title: z.string(),
  direction: z.string().default(""),
  key_points: z.array(z.string()).default([]),
  analysts: z.array(z.string()).default([]),
});
export type QaSelectPlanEntry = z.infer<typeof QaSelectPlanEntrySchema>;

export const QaSelectPayloadSchema = z.object({
  message: z.string().default(""),
  section_plan: z.array(QaSelectPlanEntrySchema).default([]),
  sections: z.array(QaSelectSectionSchema).default([]),
});
export type QaSelectPayload = z.infer<typeof QaSelectPayloadSchema>;

/** 게이트 payload 파싱 - 형태가 다르거나 섹션이 없으면 null(제네릭 게이트 UI로 폴백). */
export function parseQaSelectPayload(payload: unknown): QaSelectPayload | null {
  const parsed = QaSelectPayloadSchema.safeParse(payload);
  if (!parsed.success || parsed.data.sections.length === 0) return null;
  return parsed.data;
}

export interface DecideQaSelectInput {
  projectId: string;
  /** 섹션 id → 선택한 후보 id */
  selections: Record<string, string>;
}

export async function decideQaSelect(input: DecideQaSelectInput): Promise<RunResumeResponse> {
  return postDecision(input.projectId, { selections: input.selections });
}

export function useDecideQaSelect() {
  return useMutation({
    mutationKey: ["checkpoints", "decide-qa-select"],
    mutationFn: decideQaSelect,
  });
}

// ─── source_pool 게이트 payload (백엔드 pipeline._source_pool_gate 미러) ───
// 자료 풀의 각 출처는 사람이 취사선택할 신호(신뢰도·매칭섹션·최신성·미리보기·색인여부)를
// 함께 싣고 내려온다(SourceRef.model_dump). 제외한 출처는 결정에 excluded_source_ids로 실어
// 보내면 runner가 is_included=false로 반영해 작성 단계 검색에서 뺀다.

export const SourcePoolSourceSchema = z.object({
  id: z.string(),
  source_type: z.string().default("web_search"),
  title: z.string().nullable().optional(),
  url: z.string().nullable().optional(),
  reliability: z.string().nullable().optional(),
  matched_sections: z.array(z.string()).default([]),
  page_age: z.string().nullable().optional(),
  preview: z.string().nullable().optional(),
  has_content: z.boolean().default(true),
});
export type SourcePoolSource = z.infer<typeof SourcePoolSourceSchema>;

// 자료량 신호 - 미달은 차단이 아니라 '추가 조사' 유도(백엔드 research_min_sources).
export const SourcePoolCoverageSchema = z.object({
  n_sources: z.number().int(),
  min_required: z.number().int(),
  sufficient: z.boolean(),
  // 매칭 자료가 0건인 절("N.N 제목") - 해당 절 재료가 풀에 없다는 신호(추가 검색 유도)
  uncovered_sections: z.array(z.string()).default([]),
});
export type SourcePoolCoverage = z.infer<typeof SourcePoolCoverageSchema>;

export const SourcePoolPayloadSchema = z.object({
  message: z.string().default(""),
  section_plan: z.array(QaSelectPlanEntrySchema).default([]),
  sources: z.array(SourcePoolSourceSchema).default([]),
  coverage: SourcePoolCoverageSchema.nullish(),
});
export type SourcePoolPayload = z.infer<typeof SourcePoolPayloadSchema>;

/** source_pool 게이트 payload 파싱 - 형태가 맞지 않으면 null(제네릭 게이트 UI로 폴백). */
export function parseSourcePoolPayload(payload: unknown): SourcePoolPayload | null {
  const parsed = SourcePoolPayloadSchema.safeParse(payload);
  if (!parsed.success) return null;
  return parsed.data;
}

export interface DecideSourcePoolInput {
  projectId: string;
  /** 사람이 자료 풀에서 제외한 출처 id들 - runner가 is_included=false로 반영. */
  excludedSourceIds: string[];
  action?: CheckpointDecision;
}

export async function decideSourcePool(input: DecideSourcePoolInput): Promise<RunResumeResponse> {
  return postDecision(input.projectId, {
    action: input.action ?? "approve",
    excluded_source_ids: input.excludedSourceIds,
  });
}

export function useDecideSourcePool() {
  return useMutation({
    mutationKey: ["checkpoints", "decide-source-pool"],
    mutationFn: decideSourcePool,
  });
}

/** '추가 조사' - 게이트를 닫지 않고 보충 수집 1라운드 후 자료 검토가 다시 열린다. */
export async function decideCollectMore(projectId: string): Promise<RunResumeResponse> {
  return postDecision(projectId, { action: "collect_more" });
}

export function useDecideCollectMore() {
  return useMutation({
    mutationKey: ["checkpoints", "decide-collect-more"],
    mutationFn: decideCollectMore,
  });
}
