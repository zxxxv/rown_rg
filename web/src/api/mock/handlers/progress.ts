import { HttpResponse, http } from "msw";
import { DEMO_PROJECTS } from "@/api/mock/fixtures/projects";
import { getAnyRunnerState } from "@/api/mock/fixtures/scenarios/state";
import type { PhaseName } from "@/api/ws-messages";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

// 시나리오 phase → 백엔드 ProjectStage
const PHASE_TO_STATUS: Record<PhaseName, string> = {
  research: "researching",
  indexing: "indexing",
  writing: "writing",
  qa: "reviewing",
  export: "completed",
};

// 백엔드 routers/projects.py _STAGE_PERCENT 미러 (단계 기반 근사 진행률)
const STAGE_PERCENT: Record<string, number> = {
  created: 0,
  researching: 20,
  indexing: 40,
  writing: 60,
  reviewing: 85,
  completed: 100,
  archived: 100,
};

export const progressHandlers = [
  // 실계약: GET /projects/{id}/progress
  //   → {project_id, status, pending_gate|null, percent, tokens_used, cost_usd}
  http.get(url("projects/:id/progress"), ({ params }) => {
    const projectId = String(params.id);
    const state = getAnyRunnerState(projectId);

    if (!state) {
      // 프론트 status 어휘 = 백엔드 ProjectStage — 픽스처 값을 그대로 통과시킨다.
      const project = DEMO_PROJECTS.find((p) => p.id === projectId);
      const status = project?.status ?? "researching";
      return HttpResponse.json(
        {
          data: {
            project_id: projectId,
            status,
            pending_gate: null,
            percent: STAGE_PERCENT[status] ?? 0,
            tokens_used: 0,
            cost_usd: 0,
          },
        },
        { status: 200 },
      );
    }

    const status =
      state.finished && state.completed_phases.includes("export")
        ? "completed"
        : (PHASE_TO_STATUS[state.active_phase] ?? "researching");
    const pending_gate = state.pending_checkpoint_id
      ? {
          review_point_id: state.pending_checkpoint_id,
          gate: state.active_phase === "qa" ? "qa_select" : "source_pool",
          payload: {},
        }
      : null;

    return HttpResponse.json(
      {
        data: {
          project_id: projectId,
          status,
          pending_gate,
          percent: STAGE_PERCENT[status] ?? 0,
          tokens_used: state.tokens_used,
          cost_usd: state.cost_usd,
        },
      },
      { status: 200 },
    );
  }),
];
