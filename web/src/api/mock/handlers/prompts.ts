import { HttpResponse, http } from "msw";
import { nextPromptId, PERSONAL_PROMPTS, SYSTEM_PROMPTS } from "@/api/mock/fixtures/prompts";
import type { PersonalPrompt } from "@/api/prompts";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

function notFound() {
  return HttpResponse.json(
    { error: { code: "PROMPT_NOT_FOUND", message: "프롬프트를 찾을 수 없습니다." } },
    { status: 404 },
  );
}

export const promptsHandlers = [
  http.get(url("prompts/personal"), ({ request }) => {
    const kind = new URL(request.url).searchParams.get("kind");
    const items = kind ? PERSONAL_PROMPTS.filter((p) => p.kind === kind) : PERSONAL_PROMPTS;
    return HttpResponse.json({ data: items }, { status: 200 });
  }),

  http.post(url("prompts/personal"), async ({ request }) => {
    const body = (await request.json()) as Partial<PersonalPrompt>;
    if (!body.kind || !body.name?.trim() || !body.content?.trim()) {
      return HttpResponse.json(
        { error: { code: "VALIDATION_ERROR", message: "kind·name·content가 필요합니다." } },
        { status: 422 },
      );
    }
    const created: PersonalPrompt = {
      id: nextPromptId(),
      kind: body.kind,
      name: body.name.trim(),
      content: body.content,
      base_ref: body.base_ref ?? null,
      cat: body.cat ?? null,
      description: body.description ?? null,
      spec: { queries: [], sections: {}, ...(body.spec ?? {}) },
      updated_at: new Date().toISOString(),
    };
    PERSONAL_PROMPTS.unshift(created);
    return HttpResponse.json({ data: created }, { status: 201 });
  }),

  http.get(url("prompts/personal/:id"), ({ params }) => {
    const row = PERSONAL_PROMPTS.find((p) => p.id === String(params.id));
    return row ? HttpResponse.json({ data: row }, { status: 200 }) : notFound();
  }),

  http.patch(url("prompts/personal/:id"), async ({ params, request }) => {
    const row = PERSONAL_PROMPTS.find((p) => p.id === String(params.id));
    if (!row) return notFound();
    const body = (await request.json()) as { name?: string; content?: string };
    if (body.name !== undefined) row.name = body.name;
    if (body.content !== undefined) row.content = body.content;
    row.updated_at = new Date().toISOString();
    return HttpResponse.json({ data: row }, { status: 200 });
  }),

  http.delete(url("prompts/personal/:id"), ({ params }) => {
    const idx = PERSONAL_PROMPTS.findIndex((p) => p.id === String(params.id));
    if (idx < 0) return notFound();
    PERSONAL_PROMPTS.splice(idx, 1);
    return new HttpResponse(null, { status: 204 });
  }),

  http.get(url("prompts/system"), ({ request }) => {
    const kind = new URL(request.url).searchParams.get("kind");
    const items = kind ? SYSTEM_PROMPTS.filter((p) => p.kind === kind) : SYSTEM_PROMPTS;
    return HttpResponse.json({ data: items }, { status: 200 });
  }),

  http.get(url("prompts/system/:kind/:ref"), ({ params }) => {
    const row = SYSTEM_PROMPTS.find(
      (p) => p.kind === String(params.kind) && p.ref === decodeURIComponent(String(params.ref)),
    );
    return row ? HttpResponse.json({ data: row }, { status: 200 }) : notFound();
  }),
];
