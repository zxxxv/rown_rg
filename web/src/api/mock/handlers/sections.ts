import { HttpResponse, http } from "msw";
import { buildSectionContent } from "@/api/mock/fixtures/section-content";
import { findSectionStatus, findSectionTitle, SECTION_TREE } from "@/api/mock/fixtures/sections";
import { findSourceRef } from "@/api/mock/fixtures/source-refs";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

export const sectionsHandlers = [
  http.get(url("projects/:id/sections"), () => {
    return HttpResponse.json({ data: { tree: SECTION_TREE } }, { status: 200 });
  }),

  http.get(url("projects/:id/sections/:sec"), ({ params }) => {
    const sectionId = String(params.sec);
    const title = findSectionTitle(sectionId);
    const status = findSectionStatus(sectionId);
    if (!title || !status) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "섹션을 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    if (status !== "completed") {
      return HttpResponse.json(
        {
          error: {
            code: "section_not_ready",
            message: "아직 작성되지 않은 섹션입니다.",
          },
        },
        { status: 409 },
      );
    }
    const content = buildSectionContent(sectionId, title);
    return HttpResponse.json({ data: content }, { status: 200 });
  }),

  http.get(url("sources/:srcId"), ({ params }) => {
    const srcId = String(params.srcId);
    const source = findSourceRef(srcId);
    if (!source) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "자료를 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    return HttpResponse.json({ data: source }, { status: 200 });
  }),
];
