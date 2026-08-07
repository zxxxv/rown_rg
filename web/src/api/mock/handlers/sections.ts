import { HttpResponse, http } from "msw";
import { buildSectionContent } from "@/api/mock/fixtures/section-content";
import { findSectionStatus, findSectionTitle, SECTION_TREE } from "@/api/mock/fixtures/sections";
import { findSourceRef } from "@/api/mock/fixtures/source-refs";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

// 수동 편집·AI 재작성 결과를 세션 동안 유지하는 오버라이드 저장소(섹션 id → 본문).
const CONTENT_OVERRIDES = new Map<string, string>();

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
    if (!content) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "섹션을 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    const override = CONTENT_OVERRIDES.get(sectionId);
    if (override !== undefined) content.content = override;
    return HttpResponse.json({ data: content }, { status: 200 });
  }),

  http.patch(url("projects/:id/sections/:sec"), async ({ params, request }) => {
    const sectionId = String(params.sec);
    const title = findSectionTitle(sectionId);
    if (!title) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "섹션을 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    const body = (await request.json()) as { content?: string };
    CONTENT_OVERRIDES.set(sectionId, body.content ?? "");
    const content = buildSectionContent(sectionId, title);
    if (!content) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "섹션을 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    content.content = body.content ?? "";
    return HttpResponse.json({ data: content }, { status: 200 });
  }),

  http.post(url("projects/:id/sections/:sec/rewrite"), async ({ params, request }) => {
    const sectionId = String(params.sec);
    const title = findSectionTitle(sectionId);
    if (!title) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "섹션을 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    const body = (await request.json()) as { instruction?: string };
    const note = body.instruction?.trim()
      ? `\n\n> (AI 재작성 지시: ${body.instruction.trim()})`
      : "";
    const rewritten = `## ${title}\n\n(AI가 프로젝트 자료를 근거로 이 섹션을 다시 작성했습니다.)${note}`;
    CONTENT_OVERRIDES.set(sectionId, rewritten);
    const content = buildSectionContent(sectionId, title);
    if (!content) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "섹션을 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    content.content = rewritten;
    content.qa_status = "passed";
    return HttpResponse.json({ data: content }, { status: 200 });
  }),

  http.post(url("projects/:id/sections/:sec/rewrite-block"), async ({ params, request }) => {
    const sectionId = String(params.sec);
    const title = findSectionTitle(sectionId);
    if (!title) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "섹션을 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    const body = (await request.json()) as { block?: string; instruction?: string };
    const base = buildSectionContent(sectionId, title);
    if (!base) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "섹션을 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    const current = CONTENT_OVERRIDES.get(sectionId) ?? base.content;
    const block = body.block ?? "";
    if (!block || !current.includes(block)) {
      // 실백엔드 BLOCK_NOT_FOUND 미러 — 본문이 갱신돼 블록이 사라진 경우
      return HttpResponse.json(
        { error: { code: "BLOCK_NOT_FOUND", message: "지정한 블록을 본문에서 찾을 수 없습니다." } },
        { status: 400 },
      );
    }
    const note = body.instruction?.trim() ? ` (지시: ${body.instruction.trim()})` : "";
    const updated = current.replace(block, `(블록 재작성 결과${note})`);
    CONTENT_OVERRIDES.set(sectionId, updated);
    base.content = updated;
    return HttpResponse.json({ data: base }, { status: 200 });
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
