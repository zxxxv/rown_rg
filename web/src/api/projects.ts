import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/api/client";
import { type Project, type ProjectConfig, ProjectListSchema, ProjectSchema } from "@/api/types";
import type { ProjectFormValues } from "@/features/project-config/schema";

/** 목록 페이지 크기 — 배열 길이 < limit 이면 다음 페이지 없음으로 판단한다. */
export const PROJECT_PAGE_SIZE = 20;

export interface ProjectListParams {
  limit?: number;
  offset?: number;
}

export const projectKeys = {
  all: ["projects"] as const,
  list: (params: ProjectListParams) => [...projectKeys.all, "list", params] as const,
  listInfinite: (pageSize: number) => [...projectKeys.all, "list-infinite", pageSize] as const,
  detail: (id: string) => [...projectKeys.all, "detail", id] as const,
};

// 실백엔드 계약: GET /projects?limit&offset → ProjectRead[] (봉투·total 없음, 최신순 고정)
export async function getProjectList(params: ProjectListParams = {}): Promise<Project[]> {
  const searchParams: Record<string, string> = {};
  if (params.limit !== undefined) searchParams.limit = String(params.limit);
  if (params.offset !== undefined) searchParams.offset = String(params.offset);

  const data = await apiClient.get<unknown>("projects", { searchParams });
  return ProjectListSchema.parse(data);
}

/** 단순 목록(사이드바 등) — 상태·검색 필터는 클라이언트에서 수행한다. */
export function useProjectList(params: ProjectListParams = {}) {
  return useQuery({
    queryKey: projectKeys.list(params),
    queryFn: () => getProjectList(params),
  });
}

/** 목록 화면용 offset 무한 스크롤 — 반환 배열 길이 < pageSize면 마지막 페이지. */
export function useProjectListInfinite(pageSize = PROJECT_PAGE_SIZE) {
  return useInfiniteQuery({
    queryKey: projectKeys.listInfinite(pageSize),
    queryFn: ({ pageParam }) => getProjectList({ limit: pageSize, offset: pageParam }),
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
