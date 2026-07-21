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

export const progressHandlers = [
  // 실계약: GET /projects/{id}/progress → {project_id, status, pending_gate|null}
  http.get(url("projects/:id/progress"), ({ params }) => {
    const projectId = String(params.id);
    const state = getAnyRunnerState(projectId);

    if (!state) {
      const project = DEMO_PROJECTS.find((p) => p.id === projectId);
      const status = project?.status === "completed" ? "completed" : "researching";
      return HttpResponse.json(
        { data: { project_id: projectId, status, pending_gate: null } },
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
      { data: { project_id: projectId, status, pending_gate } },
      { status: 200 },
    );
  }),
];
