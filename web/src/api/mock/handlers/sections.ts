import { HttpResponse, http } from "msw";
import { buildSectionContent } from "@/api/mock/fixtures/section-content";
import { SECTION_LOCKS } from "@/api/mock/fixtures/section-state";
import { findSectionStatus, findSectionTitle, SECTION_TREE } from "@/api/mock/fixtures/sections";
import { findSourceRef } from "@/api/mock/fixtures/source-refs";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

// 수동 편집·AI 재작성 결과를 세션 동안 유지하는 오버라이드 저장소(섹션 id → 본문).
const CONTENT_OVERRIDES = new Map<string, string>();
// 뽑아 둔 안 - 목 세션 동안만 산다(실백엔드는 절 meta에 쌓는다).
type MockVariant = {
  id: string;
  content: string;
  n_chars: number;
  n_markers: number;
  evidence_count: number;
  volume_scaled: boolean;
};
const SECTION_VARIANTS = new Map<string, MockVariant[]>();

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
    const content = buildSectionContent(sectionId, title);
    if (!content) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "섹션을 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    if (status === "pending" || status === "failed") {
      // 실백엔드 미러 - 미작성·실패 절도 행을 돌려준다(빈 본문, qa 대기). 이 빈 절
      // 화면이 AI 재작성 진입점이라 409로 막으면 복구 경로가 없다(2026-08-14).
      content.content = "";
      content.qa_status = "pending";
    }
    const override = CONTENT_OVERRIDES.get(sectionId);
    if (override !== undefined) content.content = override;
    content.locked = SECTION_LOCKS.get(sectionId) ?? false;
    return HttpResponse.json({ data: content }, { status: 200 });
  }),

  // PATCH /projects/{id}/sections/{sec}/lock - 절 잠금 토글(실계약 0048 미러).
  // 잠긴 절은 AI 재작성이 422로 막힌다 - 목에서도 막아야 버튼이 지키는 게 뭔지 보인다.
  http.patch(url("projects/:id/sections/:sec/lock"), async ({ params, request }) => {
    const sectionId = String(params.sec);
    const title = findSectionTitle(sectionId);
    if (!title) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "섹션을 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    const body = (await request.json()) as { locked?: boolean };
    SECTION_LOCKS.set(sectionId, Boolean(body.locked));
    const content = buildSectionContent(sectionId, title);
    if (!content) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "섹션을 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    const override = CONTENT_OVERRIDES.get(sectionId);
    if (override !== undefined) content.content = override;
    content.locked = Boolean(body.locked);
    return HttpResponse.json({ data: content }, { status: 200 });
  }),

  // GET /projects/{id}/sections/{sec}/evidence - 근거 추적(데모는 추적 불가 상태)
  http.get(url("projects/:id/sections/:sec/evidence"), ({ params }) => {
    return HttpResponse.json(
      {
        data: {
          section_id: String(params.sec),
          items: [],
          claims: [],
          aligned_count: 0,
          weak_count: 0,
          unmatched_count: 0,
          pool_size: 0,
          cited_count: 0,
          unused_count: 0,
          uncited_count: 0,
          uncited_samples: [],
          // 백엔드 계약 거울 - 주장으로 안 잡혀 어떤 검사도 못 본 줄.
          // 주장 0개 + 대조 안 함 1개는 실제로 나오는 조합이라 그대로 둔다
          // (이 상태에서 툴바가 사라지면 "왜 표시가 없지"에 답할 자리가 없다).
          uncovered: [
            "정책 대응 방향: 세대별 통행 특성을 반영한 노선 운영 계획과 환승 체계 재편 검토",
          ],
          traceable: false,
        },
      },
      { status: 200 },
    );
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

  // ─── 안 고르기(한 절 3안) ───
  // 실제로는 절 재작성을 n번 도는 백그라운드 작업이다. 목에서는 즉시 3안을 채워
  // "쌓인 뒤" 화면만 확인한다 - 진행 중 화면은 running:true로 한 번 돌려 준다.
  http.post(url("projects/:id/sections/:sec/variants"), async ({ params, request }) => {
    const sectionId = String(params.sec);
    const body = (await request.json()) as { n?: number; instruction?: string };
    const n = body.n ?? 3;
    const base = buildSectionContent(sectionId, findSectionTitle(sectionId) ?? "절");
    const source = CONTENT_OVERRIDES.get(sectionId) ?? base?.content ?? "";
    const hint = body.instruction?.trim() ? ` (지시: ${body.instruction.trim()})` : "";
    SECTION_VARIANTS.set(
      sectionId,
      Array.from({ length: n }, (_, i) => ({
        id: `var_${sectionId}_${i + 1}`,
        content: `${i + 1}안${hint}

${source}`.slice(0, 1200),
        n_chars: 900 + i * 180,
        n_markers: 3 + i,
        evidence_count: 12 + i * 2,
        volume_scaled: i === 2,
      })),
    );
    return HttpResponse.json({ data: { started: true, running: true, total: n } }, { status: 202 });
  }),

  http.get(url("projects/:id/sections/:sec/variants"), ({ params }) => {
    const variants = SECTION_VARIANTS.get(String(params.sec)) ?? [];
    return HttpResponse.json(
      {
        data: {
          running: false,
          total: variants.length,
          done: variants.length,
          failures: {},
          variants,
        },
      },
      { status: 200 },
    );
  }),

  http.delete(url("projects/:id/sections/:sec/variants"), ({ params }) => {
    SECTION_VARIANTS.delete(String(params.sec));
    return HttpResponse.json({ data: { discarded: true, cancelled: false } }, { status: 200 });
  }),

  http.post(url("projects/:id/sections/:sec/variants/:vid/adopt"), ({ params }) => {
    const sectionId = String(params.sec);
    const title = findSectionTitle(sectionId);
    const picked = (SECTION_VARIANTS.get(sectionId) ?? []).find((v) => v.id === String(params.vid));
    if (!title || !picked) {
      return HttpResponse.json(
        { error: { code: "VARIANT_NOT_FOUND", message: "그 안을 찾을 수 없습니다" } },
        { status: 404 },
      );
    }
    CONTENT_OVERRIDES.set(sectionId, picked.content);
    SECTION_VARIANTS.delete(sectionId);
    const content = buildSectionContent(sectionId, title);
    if (content) {
      content.content = picked.content;
      content.locked = SECTION_LOCKS.get(sectionId) ?? false;
    }
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
    if (SECTION_LOCKS.get(sectionId)) {
      // 실계약과 같은 봉투 - 잠금이 진짜로 막는지 목에서도 눌러 볼 수 있어야 한다.
      return HttpResponse.json(
        {
          error: {
            code: "SECTION_LOCKED",
            message: `${sectionId} 절은 잠겨 있습니다 - 먼저 잠금을 푸세요`,
          },
        },
        { status: 422 },
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
      // 실백엔드 BLOCK_NOT_FOUND 미러 - 본문이 갱신돼 블록이 사라진 경우
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
