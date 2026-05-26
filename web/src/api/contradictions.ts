import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/api/client";
import {
  type ContradictionListResponse,
  ContradictionListResponseSchema,
  ContradictionSchema,
  type ContradictionWinner,
} from "@/api/types";

export const contradictionKeys = {
  all: ["contradictions"] as const,
  list: (projectId: string) => [...contradictionKeys.all, "list", projectId] as const,
};

export async function getProjectContradictions(
  projectId: string,
): Promise<ContradictionListResponse> {
  const data = await apiClient.get<unknown>(`projects/${projectId}/contradictions`);
  return ContradictionListResponseSchema.parse(data);
}

export function useProjectContradictions(projectId: string) {
  return useQuery({
    queryKey: contradictionKeys.list(projectId),
    queryFn: () => getProjectContradictions(projectId),
    enabled: Boolean(projectId),
  });
}

export interface ResolveContradictionInput {
  projectId: string;
  cid: string;
  winner: ContradictionWinner;
  feedback?: string;
}

export function useResolveContradiction() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...contradictionKeys.all, "resolve"],
    mutationFn: async (input: ResolveContradictionInput) => {
      const data = await apiClient.post<unknown>(
        `projects/${input.projectId}/contradictions/${input.cid}/resolve`,
        {
          json: {
            winner: input.winner,
            ...(input.feedback ? { feedback: input.feedback } : {}),
          },
        },
      );
      return ContradictionSchema.parse(data);
    },
    onSuccess: (_data, variables) => {
      void qc.invalidateQueries({
        queryKey: contradictionKeys.list(variables.projectId),
      });
    },
  });
}
