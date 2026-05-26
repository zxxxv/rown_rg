import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/api/client";
import {
  type Project,
  type ProjectConfig,
  type ProjectListResponse,
  ProjectListResponseSchema,
  ProjectSchema,
  type ProjectSort,
  type ProjectStatus,
} from "@/api/types";
import type { ProjectFormValues } from "@/features/project-config/schema";

export interface ProjectFilters {
  status?: ProjectStatus;
  sort?: ProjectSort;
  q?: string;
  limit?: number;
}

export const projectKeys = {
  all: ["projects"] as const,
  list: (filters: ProjectFilters) => [...projectKeys.all, "list", filters] as const,
  detail: (id: string) => [...projectKeys.all, "detail", id] as const,
};

export async function getProjectList(filters: ProjectFilters): Promise<ProjectListResponse> {
  const searchParams: Record<string, string> = {};
  if (filters.status) searchParams.status = filters.status;
  if (filters.sort) searchParams.sort = filters.sort;
  if (filters.q) searchParams.q = filters.q;
  if (filters.limit) searchParams.limit = String(filters.limit);

  const data = await apiClient.get<unknown>("projects", { searchParams });
  return ProjectListResponseSchema.parse(data);
}

export function useProjectList(filters: ProjectFilters) {
  return useQuery({
    queryKey: projectKeys.list(filters),
    queryFn: () => getProjectList(filters),
  });
}

export async function createProject(input: ProjectFormValues): Promise<Project> {
  const data = await apiClient.post<unknown>("projects", { json: input });
  return ProjectSchema.parse(data);
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...projectKeys.all, "create"],
    mutationFn: createProject,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: projectKeys.all });
    },
  });
}

export async function getProject(id: string): Promise<Project> {
  const data = await apiClient.get<unknown>(`projects/${id}`);
  return ProjectSchema.parse(data);
}

export function useProject(id: string) {
  return useQuery({
    queryKey: projectKeys.detail(id),
    queryFn: () => getProject(id),
    enabled: Boolean(id),
  });
}

export async function updateProjectConfig(id: string, config: ProjectConfig): Promise<Project> {
  const data = await apiClient.patch<unknown>(`projects/${id}/config`, {
    json: { config },
  });
  return ProjectSchema.parse(data);
}

export function useUpdateProjectConfig(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...projectKeys.detail(id), "update"],
    mutationFn: (config: ProjectConfig) => updateProjectConfig(id, config),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: projectKeys.detail(id) });
      void qc.invalidateQueries({ queryKey: projectKeys.all });
    },
  });
}
