import { delay, HttpResponse, http } from "msw";
import {
  createUploadedSource,
  getSourcesForProject,
  patchSourceInStore,
  pushSourceToStore,
} from "@/api/mock/fixtures/sources";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

interface PatchBody {
  is_included: boolean | null;
}

export const sourcesHandlers = [
  http.get(url("projects/:id/sources"), async ({ params }) => {
    const projectId = String(params.id);
    const items = getSourcesForProject(projectId);
    return HttpResponse.json({ data: { items, total: items.length } }, { status: 200 });
  }),

  http.patch(url("projects/:id/sources/:sid"), async ({ params, request }) => {
    const projectId = String(params.id);
    const sourceId = String(params.sid);
    const body = (await request.json()) as PatchBody;
    const updated = patchSourceInStore(projectId, sourceId, {
      is_included: body.is_included,
    });
    if (!updated) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "자료를 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    return HttpResponse.json({ data: updated }, { status: 200 });
  }),

  http.post(url("projects/:id/sources/upload"), async ({ params, request }) => {
    const projectId = String(params.id);
    const formData = await request.formData();
    const file = formData.get("file");
    if (!(file instanceof File)) {
      return HttpResponse.json(
        { error: { code: "no_file", message: "업로드할 파일이 없습니다." } },
        { status: 400 },
      );
    }
    await delay(1500);
    const source = createUploadedSource(projectId, file.name, file.size);
    pushSourceToStore(projectId, source);
    return HttpResponse.json({ data: source }, { status: 201 });
  }),

  http.post(url("projects/:id/sources/finalize"), async ({ params }) => {
    const projectId = String(params.id);
    const items = getSourcesForProject(projectId);
    const included = items.filter((s) => s.is_included === true).length;
    if (included === 0) {
      return HttpResponse.json(
        {
          error: {
            code: "no_included",
            message: "최소 1개 이상의 자료를 채택해야 합니다.",
          },
        },
        { status: 400 },
      );
    }
    return HttpResponse.json(
      { data: { project_id: projectId, included_count: included } },
      { status: 200 },
    );
  }),
];
