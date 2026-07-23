import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/api/client";
import { type LibraryTreeResponse, LibraryTreeResponseSchema } from "@/api/types";
import { env } from "@/env";

export const libraryKeys = {
  all: ["library"] as const,
  tree: () => [...libraryKeys.all, "tree"] as const,
};

export async function getLibraryTree(): Promise<LibraryTreeResponse> {
  const data = await apiClient.get<unknown>("library/tree");
  return LibraryTreeResponseSchema.parse(data);
}

export function useLibraryTree() {
  return useQuery({
    queryKey: libraryKeys.tree(),
    queryFn: getLibraryTree,
  });
}

interface CreateFolderInput {
  parent_id: string | null;
  name: string;
}

export function useCreateFolder() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...libraryKeys.all, "create-folder"],
    mutationFn: async (input: CreateFolderInput) => {
      return apiClient.post<unknown>("library/folders", { json: input });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: libraryKeys.all });
    },
  });
}

interface UploadFileInput {
  parent_id: string | null;
  file: File;
}

export function useUploadFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...libraryKeys.all, "upload-file"],
    mutationFn: async (input: UploadFileInput) => {
      const fd = new FormData();
      fd.append("file", input.file);
      if (input.parent_id) fd.append("parent_id", input.parent_id);
      return apiClient.post<unknown>("library/files", { body: fd });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: libraryKeys.all });
    },
  });
}

/** 노드 삭제 — 폴더는 하위 전체 포함. 관리자 또는 생성자만(그 외 403). */
export function useDeleteNode() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...libraryKeys.all, "delete-node"],
    mutationFn: async (nodeId: string) => {
      await apiClient.delete<void>(`library/nodes/${nodeId}`);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: libraryKeys.all });
    },
  });
}

interface SetVisibilityInput {
  nodeId: string;
  visible_to_roles: string[];
}

/** 파일 열람 권한(역할) 변경 — 관리자 전용. 빈 배열=전체 공개. */
export function useSetNodeVisibility() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...libraryKeys.all, "set-visibility"],
    mutationFn: async ({ nodeId, visible_to_roles }: SetVisibilityInput) => {
      return apiClient.patch<unknown>(`library/nodes/${nodeId}/visibility`, {
        json: { visible_to_roles },
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: libraryKeys.all });
    },
  });
}

/** 파일 다운로드 URL(쿠키 인증, same-origin) — <a href> 또는 프로그램적 클릭에 사용. */
export function libraryFileDownloadUrl(nodeId: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/library/files/${nodeId}/download`;
}
