import { HttpResponse, http } from "msw";
import { resolveAnyPendingCheckpoint } from "@/api/mock/fixtures/scenarios/state";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

interface DecideBody {
  decision?: Record<string, unknown>;
}

export const checkpointsHandlers = [
  // 실계약: POST /projects/{id}/decide - body {decision: dict}, 서버는 최신 pending 게이트를 해소
  http.post(url("projects/:id/decide"), async ({ params, request }) => {
    const projectId = String(params.id);
    const body = (await request.json()) as DecideBody;
    if (!body || typeof body.decision !== "object" || body.decision === null) {
      return HttpResponse.json(
        { error: { code: "validation_failed", message: "decision 객체가 필요합니다." } },
        { status: 422 },
      );
    }
    const ok = resolveAnyPendingCheckpoint(projectId);
    if (!ok) {
      return HttpResponse.json(
        { error: { code: "NO_PENDING_GATE", message: "대기 중인 검토 게이트가 없습니다" } },
        { status: 422 },
      );
    }
    return HttpResponse.json(
      { data: { project_id: projectId, status: "researching" } },
      { status: 200 },
    );
  }),
];
