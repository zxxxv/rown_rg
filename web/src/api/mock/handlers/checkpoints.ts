import { HttpResponse, http } from "msw";
import { decideCheckpointForProject } from "@/api/mock/fixtures/scenarios/state";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

interface DecideBody {
  decision: "approve" | "request_changes" | "halt" | "deeper";
  feedback?: string;
}

export const checkpointsHandlers = [
  http.post(url("projects/:id/checkpoints/:cid/decide"), async ({ params, request }) => {
    const projectId = String(params.id);
    const cid = String(params.cid);
    const body = (await request.json()) as DecideBody;
    const ok = decideCheckpointForProject(projectId, cid);
    if (!ok) {
      return HttpResponse.json(
        {
          error: {
            code: "checkpoint_not_pending",
            message: "현재 대기 중인 검토 지점이 없습니다.",
          },
        },
        { status: 409 },
      );
    }
    return HttpResponse.json(
      {
        data: {
          checkpoint_id: cid,
          decision: body.decision,
          accepted_at: new Date().toISOString(),
        },
      },
      { status: 200 },
    );
  }),
];
