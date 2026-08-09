import { HttpResponse, http } from "msw";
import { LIBRARY_TREE, syncPromptFolders } from "@/api/mock/fixtures/library";
import { DEMO_ADMIN_USER } from "@/api/mock/fixtures/users";
import type { LibraryNode } from "@/api/types";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

function findFolder(
  tree: LibraryNode[],
  id: string,
): Extract<LibraryNode, { type: "folder" }> | null {
  for (const node of tree) {
    if (node.type !== "folder") continue;
    if (node.id === id) return node;
    const sub = findFolder(node.children, id);
    if (sub) return sub;
  }
  return null;
}

function removeNode(tree: LibraryNode[], id: string): boolean {
  const idx = tree.findIndex((n) => n.id === id);
  if (idx >= 0) {
    tree.splice(idx, 1);
    return true;
  }
  return tree.some((n) => n.type === "folder" && removeNode(n.children, id));
}

function findFile(tree: LibraryNode[], id: string): Extract<LibraryNode, { type: "file" }> | null {
  for (const node of tree) {
    if (node.type === "file" && node.id === id) return node;
    if (node.type === "folder") {
      const sub = findFile(node.children, id);
      if (sub) return sub;
    }
  }
  return null;
}

/** 쓰기 대상 폴더 결정 - 상위 폴더가 있으면 그 폴더, 없으면 개인(me-files)/회사(company) 루트. */
function pickContainer(
  parentId: string | null | undefined,
  isPersonal: boolean | undefined,
): Extract<LibraryNode, { type: "folder" }> | null {
  if (parentId) return findFolder(LIBRARY_TREE, parentId);
  return findFolder(LIBRARY_TREE, isPersonal ? "me-files" : "company");
}

export const libraryHandlers = [
  http.get(url("library/tree"), () => {
    syncPromptFolders(); // 개인 프롬프트 CRUD를 트리에 반영
    return HttpResponse.json({ data: { tree: LIBRARY_TREE } }, { status: 200 });
  }),

  http.post(url("library/folders"), async ({ request }) => {
    const body = (await request.json()) as {
      name?: string;
      parent_id?: string | null;
      is_personal?: boolean;
    };
    if (!body.name?.trim()) {
      return HttpResponse.json(
        { error: { code: "VALIDATION_ERROR", message: "폴더 이름을 입력하세요." } },
        { status: 422 },
      );
    }
    const id = `dir_${crypto.randomUUID().slice(0, 8)}`;
    const scope = body.is_personal ? "personal" : "company";
    const folder: LibraryNode = {
      id,
      name: body.name.trim(),
      type: "folder",
      children: [],
      writable: { parent_id: id, scope },
    };
    // 최상위(parent 없음)는 개인/회사 컨테이너로 라우팅, 아니면 해당 폴더 안으로.
    const container = pickContainer(body.parent_id, body.is_personal);
    if (container) container.children.push(folder);
    return HttpResponse.json({ data: folder }, { status: 201 });
  }),

  http.post(url("library/files"), async ({ request }) => {
    const fd = await request.formData();
    const file = fd.get("file");
    if (!(file instanceof File)) {
      return HttpResponse.json(
        { error: { code: "VALIDATION_ERROR", message: "파일이 필요합니다." } },
        { status: 422 },
      );
    }
    const parentId = fd.get("parent_id");
    const isPersonal = fd.get("is_personal") === "true";
    const node: LibraryNode = {
      id: `file_${crypto.randomUUID().slice(0, 8)}`,
      name: file.name,
      type: "file",
      file_meta: {
        size_bytes: file.size,
        registered_at: new Date().toISOString(),
        registered_by: DEMO_ADMIN_USER.name,
        source_kind: "upload",
        visible_to_roles: ["viewer", "worker", "admin", "super_admin"],
      },
    };
    const container = pickContainer(typeof parentId === "string" ? parentId : null, isPersonal);
    if (container) container.children.push(node);
    return HttpResponse.json({ data: node }, { status: 201 });
  }),

  http.delete(url("library/nodes/:id"), ({ params }) => {
    const removed = removeNode(LIBRARY_TREE, String(params.id));
    if (!removed) {
      return HttpResponse.json(
        { error: { code: "NODE_NOT_FOUND", message: "노드를 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    return new HttpResponse(null, { status: 204 });
  }),

  http.patch(url("library/nodes/:id/visibility"), async ({ params, request }) => {
    const body = (await request.json()) as { visible_to_roles?: string[] };
    const file = findFile(LIBRARY_TREE, String(params.id));
    if (!file) {
      return HttpResponse.json(
        { error: { code: "NODE_NOT_FOUND", message: "노드를 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    file.file_meta = {
      ...file.file_meta,
      visible_to_roles: (body.visible_to_roles ?? []) as typeof file.file_meta.visible_to_roles,
    };
    return HttpResponse.json(
      { data: { visible_to_roles: file.file_meta.visible_to_roles } },
      { status: 200 },
    );
  }),

  http.get(url("library/files/:id/download"), ({ params }) => {
    const file = findFile(LIBRARY_TREE, String(params.id));
    if (!file) {
      return HttpResponse.json(
        { error: { code: "NODE_NOT_FOUND", message: "노드를 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    return new HttpResponse(`(mock) ${file.name}`, {
      status: 200,
      headers: {
        "Content-Type": "text/plain; charset=utf-8",
        "Content-Disposition": `attachment; filename="${encodeURIComponent(file.name)}"`,
      },
    });
  }),

  // AI 수집 자료의 수집 원문(content_md) - 노드 id로 파일을 찾아 제목 기반 목업 본문을 낸다.
  http.get(url("library/sources/:id/content"), ({ params }) => {
    const file = findFile(LIBRARY_TREE, String(params.id));
    const title = file?.name ?? "수집 자료";
    const contentMd = `# ${title}\n\n이것은 목업(mock) 수집 원문입니다. 실제 환경에서는 AI가 웹에서 수집해 저장한 마크다운 본문이 여기에 표시됩니다.\n\n- 원격/하이브리드 근무와 조직 내 소통에 관한 핵심 요지\n- 수집 시점의 전체 텍스트가 청킹·색인 전에 그대로 보존됩니다.\n\n> 참고: 이 화면은 라이브러리 안에서 원문을 바로 확인하기 위한 뷰어입니다.`;
    return HttpResponse.json(
      {
        data: {
          title,
          url: file?.download_url ?? null,
          reliability: "medium",
          content_md: contentMd,
          char_count: contentMd.length,
          byte_count: new TextEncoder().encode(contentMd).length,
        },
      },
      { status: 200 },
    );
  }),
];
