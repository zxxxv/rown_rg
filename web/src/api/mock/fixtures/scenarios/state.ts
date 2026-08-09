import type { PhaseName, ProgressMessage } from "@/api/ws-messages";
import { pickScenario } from "./index";

export interface ScenarioState {
  scenario_key: string;
  active_phase: PhaseName;
  phase_status: "started" | "completed";
  completed_phases: PhaseName[];
  active_step: string | null;
  tokens_used: number;
  cost_usd: number;
  eta_seconds: number | null;
  pending_checkpoint_id: string | null;
  finished: boolean;
  failed: boolean;
  last_error: { code: string; message: string } | null;
}

interface ClientHandle {
  send: (data: string) => void;
}

function initialState(scenarioKey: string): ScenarioState {
  return {
    scenario_key: scenarioKey,
    active_phase: "research",
    phase_status: "started",
    completed_phases: [],
    active_step: null,
    tokens_used: 0,
    cost_usd: 0,
    eta_seconds: null,
    pending_checkpoint_id: null,
    finished: false,
    failed: false,
    last_error: null,
  };
}

export class ScenarioRunner {
  state: ScenarioState;
  private clients = new Set<ClientHandle>();

  constructor(scenarioKey: string) {
    this.state = initialState(scenarioKey);
    void this.run();
  }

  private async run() {
    const scenarioFactory = pickScenario(this.state.scenario_key);
    try {
      for await (const msg of scenarioFactory()) {
        this.applyToState(msg);
        this.broadcast(msg);
      }
    } catch (err) {
      console.error("[ScenarioRunner] error", err);
      this.state.failed = true;
    } finally {
      this.state.finished = true;
    }
  }

  private applyToState(msg: ProgressMessage) {
    switch (msg.type) {
      case "phase":
        if (msg.status === "started") {
          this.state.active_phase = msg.phase;
          this.state.phase_status = "started";
        } else {
          if (!this.state.completed_phases.includes(msg.phase)) {
            this.state.completed_phases.push(msg.phase);
          }
          this.state.phase_status = "completed";
          this.state.active_step = null;
        }
        break;
      case "step":
        if (msg.status === "started") {
          this.state.active_step = msg.step;
          if (msg.eta_seconds !== undefined) {
            this.state.eta_seconds = msg.eta_seconds;
          }
        } else if (msg.status === "failed") {
          this.state.failed = true;
        }
        break;
      case "cost":
        this.state.tokens_used = msg.tokens_used;
        this.state.cost_usd = msg.cost_usd;
        break;
      case "checkpoint":
        this.state.pending_checkpoint_id = msg.checkpoint_id;
        break;
      case "error":
        this.state.failed = true;
        this.state.last_error = { code: msg.code, message: msg.message };
        break;
    }
  }

  private broadcast(msg: ProgressMessage) {
    const payload = JSON.stringify(msg);
    for (const c of this.clients) {
      try {
        c.send(payload);
      } catch (e) {
        console.warn("[ScenarioRunner] broadcast failed", e);
      }
    }
  }

  subscribe(client: ClientHandle): () => void {
    this.clients.add(client);
    return () => this.clients.delete(client);
  }

  decideCheckpoint(checkpointId: string): boolean {
    if (this.state.pending_checkpoint_id !== checkpointId) return false;
    this.state.pending_checkpoint_id = null;
    return true;
  }
}

const runners = new Map<string, ScenarioRunner>();

function runnerKey(projectId: string, scenarioKey: string): string {
  return `${projectId}:${scenarioKey}`;
}

export function getOrCreateRunner(projectId: string, scenarioKey: string): ScenarioRunner {
  const key = runnerKey(projectId, scenarioKey);
  let runner = runners.get(key);
  if (!runner) {
    runner = new ScenarioRunner(scenarioKey);
    runners.set(key, runner);
  }
  return runner;
}

export function getAnyRunnerState(projectId: string): ScenarioState | null {
  for (const [k, r] of runners) {
    if (k.startsWith(`${projectId}:`)) return r.state;
  }
  return null;
}

export function decideCheckpointForProject(projectId: string, checkpointId: string): boolean {
  for (const [k, r] of runners) {
    if (k.startsWith(`${projectId}:`) && r.decideCheckpoint(checkpointId)) {
      return true;
    }
  }
  return false;
}

/** 실계약(POST /projects/{id}/decide)용 - 서버처럼 "최신 pending 게이트"를 id 없이 해소한다. */
export function resolveAnyPendingCheckpoint(projectId: string): boolean {
  for (const [k, r] of runners) {
    const pending = r.state.pending_checkpoint_id;
    if (k.startsWith(`${projectId}:`) && pending && r.decideCheckpoint(pending)) {
      return true;
    }
  }
  return false;
}
