import { delay, HttpResponse, http } from "msw";
import {
  createUploadedSource,
  getSourcesForProject,
  patchSourceInStore,
  pushSourceToStore,
  removeSourceFromStore,
} from "@/api/mock/fixtures/sources";
import type { Source } from "@/api/types";
import { env } from "@/env";

// 목 fixture는 옛 화면 모양(Source)으로 적혀 있는데 실계약은 SourceItemRead다.
// 그대로 내보내면 클라이언트의 z.array(SourceItemSchema) 파싱이 죽어 화면이 "총 0"이
// 된다 - 목 모드에서 자료 검토가 계속 비어 있던 이유였다(2026-08-26 발견).
// fixture를 통째로 옮기는 대신 여기서 실계약 모양으로 바꿔 내보낸다.
const RELIABILITY_LABEL = (v: number | undefined): "high" | "medium" | "low" => {
  if (v === undefined) return "medium";
  if (v >= 0.75) return "high";
  if (v >= 0.45) return "medium";
  return "low";
};

function toContract(s: Source): Record<string, unknown> {
  return {
    id: s.id,
    // fixture의 source_kind는 옛 분류(academic·gov·media까지 있다). 실계약은 셋뿐이라
    // 출처의 성격이 아니라 **어디서 왔는가**로 접는다 - 웹에서 온 것은 web_search다.
    source_type:
      s.source_kind === "upload" || s.source_kind === "library" ? s.source_kind : "web_search",
    title: s.title,
    url: s.url ?? null,
    reliability: RELIABILITY_LABEL(s.reliability),
    // 실계약은 boolean이다 - 목의 null(미결정)은 기본 채택으로 읽는다.
    is_included: s.is_included !== false,
    matched_sections: s.matched_sections ?? [],
    page_age: s.published_at ?? null,
    preview: s.preview ?? s.summary ?? null,
    has_content: true,
    library_node_id: s.library_file_id ?? null,
    indexing: s.indexing ?? false,
    index_deferred: s.index_deferred ?? false,
    index_error: null,
    size_bytes: null,
    page_count: null,
    n_chunks: null,
    // 목은 시각이 없다 - 목록 정렬만 쓰므로 고정값이면 충분하다(무작위는 재현을 깬다).
    created_at: "2026-08-20T00:00:00Z",
    published_year: null,
  };
}

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
    // 실백엔드는 **배열**을 준다(response_model=list[SourceItemRead]). {items,total}로
    // 감싸면 클라이언트의 z.array 파싱이 죽어 화면이 "총 0"으로 뜬다 - 목 모드에서
    // 자료 검토 화면이 계속 비어 있던 이유였다(2026-08-26 발견).
    return HttpResponse.json({ data: items.map(toContract) }, { status: 200 });
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

  // 실계약 미러: 파일 자료(업로드·라이브러리)만 삭제 가능, 웹 수집은 400.
  http.delete(url("projects/:id/sources/:sid"), async ({ params }) => {
    const projectId = String(params.id);
    const sourceId = String(params.sid);
    const target = getSourcesForProject(projectId).find((s) => s.id === sourceId);
    if (!target) {
      return HttpResponse.json(
        { error: { code: "SOURCE_NOT_FOUND", message: "자료를 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    if (target.source_kind === "web_search") {
      return HttpResponse.json(
        {
          error: {
            code: "SOURCE_NOT_DELETABLE",
            message: "웹 수집 자료는 삭제할 수 없습니다 - 제외로 처리해 주세요.",
          },
        },
        { status: 400 },
      );
    }
    removeSourceFromStore(projectId, sourceId);
    return new HttpResponse(null, { status: 204 });
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
