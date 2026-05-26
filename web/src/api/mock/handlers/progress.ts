import { HttpResponse, http } from "msw";
import { getAnyRunnerState } from "@/api/mock/fixtures/scenarios/state";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

export const progressHandlers = [
  http.get(url("projects/:id/progress/snapshot"), ({ params }) => {
    const projectId = String(params.id);
    const state = getAnyRunnerState(projectId);
    if (!state) {
      return HttpResponse.json(
        {
          data: {
            phase: "research",
            phase_status: "started",
            completed_phases: [],
            active_step: null,
            tokens_used: 0,
            cost_usd: 0,
            eta_seconds: null,
            pending_checkpoint_id: null,
          },
        },
        { status: 200 },
      );
    }
    return HttpResponse.json(
      {
        data: {
          phase: state.active_phase,
          phase_status: state.phase_status,
          completed_phases: state.completed_phases,
          active_step: state.active_step,
          tokens_used: state.tokens_used,
          cost_usd: state.cost_usd,
          eta_seconds: state.eta_seconds,
          pending_checkpoint_id: state.pending_checkpoint_id,
        },
      },
      { status: 200 },
    );
  }),
];
