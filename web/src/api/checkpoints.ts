import { useMutation } from "@tanstack/react-query";
import { apiClient } from "@/api/client";

// 실계약: POST /projects/{id}/decide — body {decision: dict}
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
  /** 화면 표시용 참조 — 서버는 항상 최신 pending 게이트를 처리하므로 전송하지 않는다. */
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
