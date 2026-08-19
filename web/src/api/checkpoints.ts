import { useMutation } from "@tanstack/react-query";
import { z } from "zod";
import { ApiError, apiClient } from "@/api/client";
import type { Outline } from "@/api/types";

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

// 검색 리허설이 자료 게이트를 다시 연 경우의 근거 공백 신호 - 색인 후 절마다 작성과
// 같은 검색을 미리 돌린 결과라, uncovered_sections(수집 매칭)보다 강한 실측 신호다.
export const RehearsalGapSchema = z.object({
  label: z.string().default(""),
  floor_passed: z.number().int().default(0),
  needed: z.number().int().default(0),
  // 앞 절 산출로 쓰는 구성형 절 - 자료를 더 모아도 검색으로는 안 채워진다
  constructive: z.boolean().default(false),
  // 요약 트리(클러스터)에도 유사 자료 없음 - 질의 문제가 아니라 자료 자체가 없다는 뜻
  raptor_gap: z.boolean().default(false),
});
export const SourcePoolRehearsalSchema = z.object({
  reopens_used: z.number().int().default(0),
  empty_sections: z.array(RehearsalGapSchema).default([]),
});
export type SourcePoolRehearsal = z.infer<typeof SourcePoolRehearsalSchema>;

export const SourcePoolPayloadSchema = z.object({
  message: z.string().default(""),
  section_plan: z.array(QaSelectPlanEntrySchema).default([]),
  sources: z.array(SourcePoolSourceSchema).default([]),
  coverage: SourcePoolCoverageSchema.nullish(),
  rehearsal: SourcePoolRehearsalSchema.nullish(),
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

// ─── design_brief 게이트 payload (백엔드 services/generation/design_brief 미러) ───
// 수집 **전** 게이트다. search_query는 설명용 근사치가 아니라 검색기가 실제로 쓰는
// 문자열이라, 여기서 본 것과 실행되는 것이 같다.

export const BriefVolumeSchema = z.object({
  min_chars: z.number().int(),
  max_chars: z.number().int(),
});

export const BriefSectionSchema = z.object({
  section_id: z.string(),
  chapter_number: z.number().int(),
  section_number: z.number().int(),
  chapter_title: z.string().default(""),
  title: z.string(),
  direction: z.string().default(""),
  key_points: z.array(z.string()).default([]),
  analysts: z.array(z.string()).default([]),
  search_query: z.string().default(""),
  volume: BriefVolumeSchema.nullish(),
});
export type BriefSection = z.infer<typeof BriefSectionSchema>;

/** 같은 검색 질의를 쓰는 절 묶음 - 이 절들은 반드시 같은 근거로 쓰인다. */
export const DuplicateQueryGroupSchema = z.object({
  query: z.string(),
  sections: z.array(
    z.object({
      chapter_number: z.number().int(),
      section_number: z.number().int(),
      label: z.string(),
    }),
  ),
});
export type DuplicateQueryGroup = z.infer<typeof DuplicateQueryGroupSchema>;

/** 시작 전 규모 추정 - 절별 분량 목표 합 + 모드별 실측 단가 범위(청구 예측 아님). */
export const BriefEstimateSchema = z.object({
  model_mode: z.string().default("standard"),
  n_sections: z.number().int().default(0),
  total_min_chars: z.number().int().default(0),
  total_max_chars: z.number().int().default(0),
  pages_min: z.number().int().default(0),
  pages_max: z.number().int().default(0),
  cost_usd_min: z.number().default(0),
  cost_usd_max: z.number().default(0),
  // 이번 달 남은 한도(사용자·조직 중 빡빡한 쪽) - 부족해도 차단하지 않는다(경고만).
  // 조회 실패 시 서버가 필드를 생략한다 - 카드가 숫자 없이 뜬다.
  remaining_limit_usd: z.number().nullish(),
  // 남은 한도 경고의 비교 기준 - 모드별 런 1회 고정 예상 비용(고급 $30/표준 $20/절약 $15).
  expected_run_cost_usd: z.number().nullish(),
});
export type BriefEstimate = z.infer<typeof BriefEstimateSchema>;

/** AI 실행 계획(제안 전용) - 절별 목표·자료 전략·작성 구성 + 절 간 흐름. 실패 시 null. */
export const AiPlanSchema = z.object({
  chapters: z
    .array(z.object({ chapter: z.number().int(), goal: z.string().default("") }))
    .default([]),
  sections: z
    .array(
      z.object({
        chapter: z.number().int(),
        section: z.number().int(),
        goal: z.string().default(""),
        source_strategy: z.string().default(""),
        writing_plan: z.string().default(""),
        // 이 절을 자료 풀에서 찾을 검색 질의 - 계획이 만들고 실제 검색이 그대로 쓴다.
        search_queries: z.array(z.string()).default([]),
      }),
    )
    .default([]),
  flows: z
    .array(z.object({ from: z.string(), to: z.string(), carries: z.string().default("") }))
    .default([]),
  orphans: z.array(z.string()).default([]),
  query_splits: z.array(z.object({ section: z.string(), query: z.string() })).default([]),
});
export type AiPlan = z.infer<typeof AiPlanSchema>;

/** 장 단위 수집 계획 - 자료 수집은 장마다 한 콜씩 돌고, 주제문은 여기서만 일한다. */
export const BriefChapterSchema = z.object({
  chapter_number: z.number().int(),
  title: z.string().default(""),
  collection_query: z.string().default(""),
  section_titles: z.array(z.string()).default([]),
});
export type BriefChapter = z.infer<typeof BriefChapterSchema>;

export const DesignBriefPayloadSchema = z.object({
  message: z.string().default(""),
  topic: z.string().default(""),
  estimate: BriefEstimateSchema.nullish(),
  ai_plan: AiPlanSchema.nullish(),
  chapters: z.array(BriefChapterSchema).default([]),
  sections: z.array(BriefSectionSchema).default([]),
  duplicate_queries: z.array(DuplicateQueryGroupSchema).default([]),
  warnings: z
    .object({
      duplicate_query_sections: z.number().int().default(0),
      sections_without_analyst: z.array(z.string()).default([]),
    })
    .default({ duplicate_query_sections: 0, sections_without_analyst: [] }),
});
export type DesignBriefPayload = z.infer<typeof DesignBriefPayloadSchema>;

/** design_brief 게이트 payload 파싱 - 형태가 맞지 않으면 null(제네릭 게이트 UI로 폴백). */
export function parseDesignBriefPayload(payload: unknown): DesignBriefPayload | null {
  const parsed = DesignBriefPayloadSchema.safeParse(payload);
  if (!parsed.success) return null;
  return parsed.data;
}

export interface DecideDesignBriefInput {
  projectId: string;
  /** 고친 목차. 생략하면 그대로 진행(승인만). 서버가 config.outline에 커밋한다. */
  outline?: Outline;
  /** replan이면 수집으로 가지 않고 고친 목차로 브리프를 다시 계산해 게이트가 다시 열린다. */
  action?: "approve" | "replan";
  /** 사람이 게이트에서 고친 절별 계획 - 서버가 AI 원안 대신 이것을 작성 계약으로 커밋한다. */
  aiPlan?: { sections: AiPlan["sections"] };
}

export async function decideDesignBrief(input: DecideDesignBriefInput): Promise<RunResumeResponse> {
  return postDecision(input.projectId, {
    action: input.action ?? "approve",
    ...(input.outline ? { outline: input.outline } : {}),
    ...(input.aiPlan ? { ai_plan: input.aiPlan } : {}),
  });
}

export function useDecideDesignBrief() {
  return useMutation({
    mutationKey: ["checkpoints", "decide-design-brief"],
    mutationFn: decideDesignBrief,
  });
}

// ─── 설계 브리프 기록 - 게이트가 닫힌 뒤 사후 열람용(GET /design-brief) ───

export const DesignBriefRecordSchema = z.object({
  status: z.string(), // pending | resolved
  payload: z.record(z.string(), z.unknown()),
  decision: z.record(z.string(), z.unknown()).nullish(),
  created_at: z.string(),
  resolved_at: z.string().nullish(),
});
export type DesignBriefRecord = z.infer<typeof DesignBriefRecordSchema>;

/** 최신 브리프 기록. 아직 브리프가 만들어진 적 없으면 null(실행 전 프로젝트). */
export async function getDesignBrief(projectId: string): Promise<DesignBriefRecord | null> {
  try {
    const data = await apiClient.get<unknown>(`projects/${projectId}/design-brief`);
    return DesignBriefRecordSchema.parse(data);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    throw err;
  }
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
