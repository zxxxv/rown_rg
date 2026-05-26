import { useCallback, useReducer } from "react";
import type { ProgressSnapshot } from "@/api/progress";
import type { PhaseName, ProgressMessage, StreamChannel } from "@/api/ws-messages";

const STREAM_MAX_CHARS = 500;
const STREAM_CHANNELS: StreamChannel[] = [
  "critic_thinking",
  "research_keywords",
  "contradiction_explain",
];

export interface ProgressUiState {
  active_phase: PhaseName;
  phase_status: "started" | "completed";
  completed_phases: Set<PhaseName>;
  current_step: string | null;
  completed_steps: Record<string, string[]>;
  failed_steps: Record<string, string[]>;
  tokens_used: number;
  cost_usd: number;
  eta_seconds: number | null;
  streams: Record<StreamChannel, string>;
  checkpoint_id: string | null;
  checkpoint_level: 1 | 2 | null;
  error: { code: string; message: string } | null;
  finished: boolean;
}

function emptyStreams(): Record<StreamChannel, string> {
  return STREAM_CHANNELS.reduce(
    (acc, c) => {
      acc[c] = "";
      return acc;
    },
    {} as Record<StreamChannel, string>,
  );
}

export function initialProgressState(): ProgressUiState {
  return {
    active_phase: "research",
    phase_status: "started",
    completed_phases: new Set(),
    current_step: null,
    completed_steps: {},
    failed_steps: {},
    tokens_used: 0,
    cost_usd: 0,
    eta_seconds: null,
    streams: emptyStreams(),
    checkpoint_id: null,
    checkpoint_level: null,
    error: null,
    finished: false,
  };
}

type Action =
  | { type: "ws"; msg: ProgressMessage }
  | { type: "snapshot"; snapshot: ProgressSnapshot }
  | { type: "clear_checkpoint" }
  | { type: "clear_error" };

function appendStream(prev: string, delta: string): string {
  const next = prev + delta;
  return next.length > STREAM_MAX_CHARS ? next.slice(next.length - STREAM_MAX_CHARS) : next;
}

function reducer(state: ProgressUiState, action: Action): ProgressUiState {
  if (action.type === "snapshot") {
    const s = action.snapshot;
    return {
      ...state,
      active_phase: s.phase,
      phase_status: s.phase_status,
      completed_phases: new Set(s.completed_phases),
      current_step: s.active_step ?? null,
      tokens_used: s.tokens_used,
      cost_usd: s.cost_usd,
      eta_seconds: s.eta_seconds ?? null,
      checkpoint_id: s.pending_checkpoint_id,
    };
  }
  if (action.type === "clear_checkpoint") {
    return { ...state, checkpoint_id: null, checkpoint_level: null };
  }
  if (action.type === "clear_error") {
    return { ...state, error: null };
  }

  const msg = action.msg;
  switch (msg.type) {
    case "phase": {
      if (msg.status === "started") {
        return { ...state, active_phase: msg.phase, phase_status: "started" };
      }
      const next = new Set(state.completed_phases);
      next.add(msg.phase);
      return {
        ...state,
        completed_phases: next,
        phase_status: "completed",
        current_step: null,
        finished: msg.phase === "export",
      };
    }
    case "step": {
      const phase = msg.phase;
      if (msg.status === "started") {
        return {
          ...state,
          current_step: msg.step,
          eta_seconds: msg.eta_seconds ?? state.eta_seconds,
        };
      }
      if (msg.status === "completed") {
        const arr = state.completed_steps[phase] ?? [];
        if (arr.includes(msg.step)) return state;
        return {
          ...state,
          completed_steps: { ...state.completed_steps, [phase]: [...arr, msg.step] },
          current_step: state.current_step === msg.step ? null : state.current_step,
        };
      }
      if (msg.status === "failed") {
        const arr = state.failed_steps[phase] ?? [];
        return {
          ...state,
          failed_steps: { ...state.failed_steps, [phase]: [...arr, msg.step] },
        };
      }
      return state;
    }
    case "stream":
      return {
        ...state,
        streams: {
          ...state.streams,
          [msg.channel]: appendStream(state.streams[msg.channel], msg.delta),
        },
      };
    case "fake_stream":
      return state;
    case "cost":
      return { ...state, tokens_used: msg.tokens_used, cost_usd: msg.cost_usd };
    case "checkpoint":
      return {
        ...state,
        checkpoint_id: msg.checkpoint_id,
        checkpoint_level: msg.level,
      };
    case "error":
      return {
        ...state,
        error: { code: msg.code, message: msg.message },
      };
    default:
      return state;
  }
}

export function useProgressState() {
  const [state, dispatch] = useReducer(reducer, undefined, initialProgressState);
  const onWsMessage = useCallback((msg: ProgressMessage) => {
    dispatch({ type: "ws", msg });
  }, []);
  const applySnapshot = useCallback((snapshot: ProgressSnapshot) => {
    dispatch({ type: "snapshot", snapshot });
  }, []);
  const clearCheckpoint = useCallback(() => {
    dispatch({ type: "clear_checkpoint" });
  }, []);
  const clearError = useCallback(() => {
    dispatch({ type: "clear_error" });
  }, []);
  return { state, onWsMessage, applySnapshot, clearCheckpoint, clearError };
}
