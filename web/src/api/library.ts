import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient, apiUrl } from "@/api/client";
import {
  type LibraryTreeResponse,
  LibraryTreeResponseSchema,
  type SourceContent,
  SourceContentSchema,
} from "@/api/types";
import { uploadWithProgress } from "@/api/upload";

export const libraryKeys = {
  all: ["library"] as const,
  tree: () => [...libraryKeys.all, "tree"] as const,
  sourceContent: (contentUrl: string) =>
    [...libraryKeys.all, "source-content", contentUrl] as const,
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
  /** 최상위 생성 시 개인(내 자료)=true / 회사 공유=false. 상위 폴더가 있으면 무시(상속). */
  is_personal?: boolean;
}

export function useCreateFolder() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...libraryKeys.all, "create-folder"],
    mutationFn: async (input: CreateFolderInput) => {
      return apiClient.post<unknown>("library/folders", {
        json: {
          parent_id: input.parent_id,
          name: input.name,
          is_personal: input.is_personal ?? false,
        },
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: libraryKeys.all });
    },
  });
}

interface UploadFileInput {
  parent_id: string | null;
  file: File;
  /** 최상위 업로드 시 개인(내 자료)=true / 회사 공유=false. 상위 폴더가 있으면 무시(상속). */
  is_personal?: boolean;
  /** 실제 전송 진행률(0~100). */
  onProgress?: (percent: number) => void;
}

// XHR 업로드 - 진행률 + 넉넉한 타임아웃. ky 기본 30초는 수십 MB 파일이 잘렸다.
export function useUploadFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: [...libraryKeys.all, "upload-file"],
    mutationFn: async (input: UploadFileInput) => {
      const fd = new FormData();
      fd.append("file", input.file);
      if (input.parent_id) fd.append("parent_id", input.parent_id);
      fd.append("is_personal", input.is_personal ? "true" : "false");
      return uploadWithProgress<unknown>("library/files", fd, { onProgress: input.onProgress });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: libraryKeys.all });
    },
  });
}

/** 노드 삭제 - 폴더는 하위 전체 포함. 관리자 또는 생성자만(그 외 403). */
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

/** 파일 열람 권한(역할) 변경 - 관리자 전용. 빈 배열=전체 공개. */
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

/** AI 수집 자료의 수집 원문(content_md) 조회 - content_url이 있을 때만 활성.
 *
 * contentUrl은 트리 노드의 상대 경로(예: "library/sources/{id}/content")를 그대로 GET한다.
 */
export function useSourceContent(contentUrl: string | null | undefined) {
  return useQuery<SourceContent>({
    queryKey: libraryKeys.sourceContent(contentUrl ?? ""),
    enabled: Boolean(contentUrl),
    queryFn: async () => {
      const data = await apiClient.get<unknown>(contentUrl as string);
      return SourceContentSchema.parse(data);
    },
  });
}

/** 파일 다운로드 URL(쿠키 인증, same-origin) - <a href> 또는 프로그램적 클릭에 사용. */
export function libraryFileDownloadUrl(nodeId: string): string {
  return apiUrl(`library/files/${nodeId}/download`);
}

/** 가상 파일의 download_url 해석 - 절대 URL(웹 출처)은 그대로, 상대경로는 API base 접두. */
export function resolveDownloadUrl(url: string): string {
  if (/^https?:\/\//.test(url)) return url;
  return apiUrl(url.replace(/^\//, ""));
}
