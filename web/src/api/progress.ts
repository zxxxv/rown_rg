import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { apiClient } from "@/api/client";
import { PhaseNameSchema } from "@/api/ws-messages";

export const ProgressSnapshotSchema = z.object({
  phase: PhaseNameSchema,
  phase_status: z.enum(["started", "completed"]),
  completed_phases: z.array(PhaseNameSchema),
  active_step: z.string().optional(),
  tokens_used: z.number().nonnegative(),
  cost_usd: z.number().nonnegative(),
  eta_seconds: z.number().nonnegative().optional(),
  pending_checkpoint_id: z.string().nullable(),
});
export type ProgressSnapshot = z.infer<typeof ProgressSnapshotSchema>;

export const progressKeys = {
  all: ["progress"] as const,
  snapshot: (projectId: string) => [...progressKeys.all, "snapshot", projectId] as const,
};

export async function getProgressSnapshot(projectId: string): Promise<ProgressSnapshot> {
  const data = await apiClient.get<unknown>(`projects/${projectId}/progress/snapshot`);
  return ProgressSnapshotSchema.parse(data);
}

export function useProgressSnapshot(projectId: string, enabled = true) {
  return useQuery({
    queryKey: progressKeys.snapshot(projectId),
    queryFn: () => getProgressSnapshot(projectId),
    enabled: enabled && Boolean(projectId),
    staleTime: 0,
  });
}
