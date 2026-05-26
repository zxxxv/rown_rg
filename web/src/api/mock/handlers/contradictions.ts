import { HttpResponse, http } from "msw";
import {
  getContradictionsForProject,
  resolveContradiction,
} from "@/api/mock/fixtures/contradictions";
import { DEMO_ADMIN_USER } from "@/api/mock/fixtures/users";
import type { ContradictionWinner } from "@/api/types";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

interface ResolveBody {
  winner: ContradictionWinner;
  feedback?: string;
}

export const contradictionsHandlers = [
  http.get(url("projects/:id/contradictions"), ({ params }) => {
    const projectId = String(params.id);
    const items = getContradictionsForProject(projectId);
    return HttpResponse.json({ data: { items } }, { status: 200 });
  }),

  http.post(url("projects/:id/contradictions/:cid/resolve"), async ({ params, request }) => {
    const projectId = String(params.id);
    const cid = String(params.cid);
    const body = (await request.json()) as ResolveBody;
    const updated = resolveContradiction(projectId, cid, body.winner, DEMO_ADMIN_USER.name);
    if (!updated) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "모순을 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    return HttpResponse.json({ data: updated }, { status: 200 });
  }),
];
