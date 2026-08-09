import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/api/client";
import {
  type Project,
  type ProjectConfig,
  ProjectListSchema,
  ProjectSchema,
  type ProjectStatus,
} from "@/api/types";
import type { ProjectFormValues } from "@/features/project-config/schema";

/** 목록 페이지 크기 — 배열 길이 < limit 이면 다음 페이지 없음으로 판단한다. */
export const PROJECT_PAGE_SIZE = 20;

/** 목록 상태 필터 — 실제 단계값 + 'in_progress'(완료·보관·취소가 아닌 진행 단계 묶음, 백엔드 그룹 필터). */
export type ProjectStatusFilter = ProjectStatus | "in_progress";

/** 서버 필터 — status는 단계값 또는 'in_progress'(오값 422), q는 제목·주제·소유자명 부분검색.
 *  scope=all은 admin·super_admin만 전체를 본다(일반 사용자는 무시하고 자기 것). */
export interface ProjectListFilters {
  status?: ProjectStatusFilter;
  q?: string;
  /** 보고서 유형(프리셋 id). 'blank'는 프리셋 없이 만든 자유 주제만 고른다. */
  preset?: string;
  scope?: "mine" | "all";
}

export interface ProjectListParams extends ProjectListFilters {
  limit?: number;
  offset?: number;
}

export const projectKeys = {
  all: ["projects"] as const,
  list: (params: ProjectListParams) => [...projectKeys.all, "list", params] as const,
  listInfinite: (pageSize: number, filters: ProjectListFilters) =>
    [...projectKeys.all, "list-infinite", pageSize, filters] as const,
  detail: (id: string) => [...projectKeys.all, "detail", id] as const,
};

// 실백엔드 계약: GET /projects?limit&offset&status&q → ProjectRead[] (봉투·total 없음, 최신순 고정)
export async function getProjectList(params: ProjectListParams = {}): Promise<Project[]> {
  const searchParams: Record<string, string> = {};
  if (params.limit !== undefined) searchParams.limit = String(params.limit);
  if (params.offset !== undefined) searchParams.offset = String(params.offset);
  if (params.status) searchParams.status = params.status;
  if (params.q) searchParams.q = params.q;
  if (params.preset) searchParams.preset = params.preset;
  if (params.scope) searchParams.scope = params.scope;

  const data = await apiClient.get<unknown>("projects", { searchParams });
  return ProjectListSchema.parse(data);
}

/** 단순 목록(사이드바 등). */
export function useProjectList(params: ProjectListParams = {}) {
  return useQuery({
    queryKey: projectKeys.list(params),
    queryFn: () => getProjectList(params),
  });
}

/** 목록 화면용 offset 무한 스크롤 — 필터가 바뀌면 쿼리 키가 바뀌어 페이지가 리셋된다. */
export function useProjectListInfinite(
  filters: ProjectListFilters = {},
  pageSize = PROJECT_PAGE_SIZE,
) {
  return useInfiniteQuery({
    queryKey: projectKeys.listInfinite(pageSize, filters),
    queryFn: ({ pageParam }) => getProjectList({ ...filters, limit: pageSize, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, _allPages, lastOffset) =>
      lastPage.length < pageSize ? undefined : lastOffset + pageSize,
  });
}

/** 백엔드 ProjectCreate 계약 — preset·depth_mode는 config 중첩이 아니라 최상위 필드다. */
export interface ProjectCreateBody {
  title: string;
  topic: string;
  preset: string | null;
  config: ProjectConfig;
  depth_mode: ProjectConfig["depth_mode"];
}

export function toProjectCreateBody(input: ProjectFormValues): ProjectCreateBody {
  // 레거시 "blank" 값·빈 문자열은 자유 주제(null)로 정규화한다.
  const preset =
    !input.config.preset || input.config.preset === "blank" ? null : input.config.preset;
  return {
    title: input.title,
    topic: input.topic,
    preset,
    config: { ...input.config, preset },
    depth_mode: input.config.depth_mode,
  };
}

export async function createProject(input: ProjectFormValues): Promise<Project> {
  const data = await apiClient.post<unknown>("projects", { json: toProjectCreateBody(input) });
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

/** POST /projects/{id}/run — 백그라운드 실행 시작(202). */
export interface RunProjectResponse {
  project_id: string;
  status: string;
}

export async function runProject(id: string): Promise<RunProjectResponse> {
  return apiClient.post<RunProjectResponse>(`projects/${id}/run`);
}

export function useRunProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...projectKeys.all, "run"],
    mutationFn: runProject,
    onSuccess: (_res, id) => {
      void qc.invalidateQueries({ queryKey: projectKeys.detail(id) });
      void qc.invalidateQueries({ queryKey: projectKeys.all });
    },
  });
}

/** POST /projects/{id}/cancel — 진행 중 실행 취소(협조적). 실행 중이면 cancelling, 아니면 cancelled. */
export interface CancelProjectResponse {
  project_id: string;
  status: "cancelling" | "cancelled";
}

export async function cancelProject(id: string): Promise<CancelProjectResponse> {
  return apiClient.post<CancelProjectResponse>(`projects/${id}/cancel`);
}

export function useCancelProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...projectKeys.all, "cancel"],
    mutationFn: cancelProject,
    onSuccess: (_res, id) => {
      void qc.invalidateQueries({ queryKey: projectKeys.detail(id) });
      void qc.invalidateQueries({ queryKey: projectKeys.all });
    },
  });
}

/** DELETE /projects/{id} — 완료·보관 상태만 허용(그 외 422). 사용량 기록은 보존된다. */
export async function deleteProject(id: string): Promise<void> {
  await apiClient.delete<void>(`projects/${id}`);
}

export function useDeleteProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...projectKeys.all, "delete"],
    mutationFn: deleteProject,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: projectKeys.all });
    },
  });
}

export async function getProject(id: string): Promise<Project> {
  const data = await apiClient.get<unknown>(`projects/${id}`);
  return ProjectSchema.parse(data);
}

export function useProject(id: string, refetchInterval?: number) {
  return useQuery({
    queryKey: projectKeys.detail(id),
    queryFn: () => getProject(id),
    enabled: Boolean(id),
    refetchInterval,
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
