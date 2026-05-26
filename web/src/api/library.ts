import { useMutation, useQuery } from "@tanstack/react-query";
import { apiClient } from "@/api/client";
import { type LibraryTreeResponse, LibraryTreeResponseSchema } from "@/api/types";

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
  return useMutation({
    mutationKey: [...libraryKeys.all, "create-folder"],
    mutationFn: async (input: CreateFolderInput) => {
      return apiClient.post<unknown>("library/folders", { json: input });
    },
  });
}

interface UploadFileInput {
  parent_id: string | null;
  file: File;
}

export function useUploadFile() {
  return useMutation({
    mutationKey: [...libraryKeys.all, "upload-file"],
    mutationFn: async (input: UploadFileInput) => {
      const fd = new FormData();
      fd.append("file", input.file);
      if (input.parent_id) fd.append("parent_id", input.parent_id);
      return apiClient.post<unknown>("library/files", { body: fd });
    },
  });
}
