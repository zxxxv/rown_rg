import { useMutation } from "@tanstack/react-query";
import { apiClient } from "@/api/client";

export type CheckpointDecision = "approve" | "request_changes" | "halt" | "deeper";

export interface DecideCheckpointInput {
  projectId: string;
  checkpointId: string;
  decision: CheckpointDecision;
  feedback?: string;
}

export async function decideCheckpoint(input: DecideCheckpointInput): Promise<void> {
  await apiClient.post<unknown>(
    `projects/${input.projectId}/checkpoints/${input.checkpointId}/decide`,
    {
      json: {
        decision: input.decision,
        ...(input.feedback ? { feedback: input.feedback } : {}),
      },
    },
  );
}

export function useDecideCheckpoint() {
  return useMutation({
    mutationKey: ["checkpoints", "decide"],
    mutationFn: decideCheckpoint,
  });
}
