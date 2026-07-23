import { HttpResponse, http } from "msw";
import { LIBRARY_TREE } from "@/api/mock/fixtures/library";
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

export const libraryHandlers = [
  http.get(url("library/tree"), () => {
    return HttpResponse.json({ data: { tree: LIBRARY_TREE } }, { status: 200 });
  }),

  http.post(url("library/folders"), async ({ request }) => {
    const body = (await request.json()) as { name?: string; parent_id?: string | null };
    if (!body.name?.trim()) {
      return HttpResponse.json(
        { error: { code: "VALIDATION_ERROR", message: "폴더 이름을 입력하세요." } },
        { status: 422 },
      );
    }
    const folder: LibraryNode = {
      id: `dir_${crypto.randomUUID().slice(0, 8)}`,
      name: body.name.trim(),
      type: "folder",
      children: [],
    };
    const parent = body.parent_id ? findFolder(LIBRARY_TREE, body.parent_id) : null;
    if (parent) parent.children.push(folder);
    else LIBRARY_TREE.push(folder);
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
    const parent = typeof parentId === "string" ? findFolder(LIBRARY_TREE, parentId) : null;
    if (parent) parent.children.push(node);
    else LIBRARY_TREE.push(node);
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
];
