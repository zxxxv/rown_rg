import { useCallback, useReducer } from "react";
import type { ProgressSnapshot } from "@/api/progress";
import type { PhaseName, ProgressMessage, StreamChannel } from "@/api/ws-messages";

const STREAM_MAX_CHARS = 500;
const DRAFT_MAX_CHARS = 8000;
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
  writing_draft: string;
  writing_section: string | null;
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
    writing_draft: "",
    writing_section: null,
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

function appendStream(prev: string, delta: string, max = STREAM_MAX_CHARS): string {
  const next = prev + delta;
  return next.length > max ? next.slice(next.length - max) : next;
}

function reducer(state: ProgressUiState, action: Action): ProgressUiState {
  if (action.type === "snapshot") {
    const s = action.snapshot;
    return {
      ...state,
      active_phase: s.phase,
      phase_status: s.phase_status,
      completed_phases: new Set(s.completed_phases),
      current_step: s.active_step ?? state.current_step,
      // 백엔드 progress 응답에는 토큰·비용·ETA가 없다 — 없으면 WS로 수신한 기존 값을 유지
      tokens_used: s.tokens_used ?? state.tokens_used,
      cost_usd: s.cost_usd ?? state.cost_usd,
      eta_seconds: s.eta_seconds ?? state.eta_seconds,
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
        // 재시도: 이전에 실패한 동일 step의 실패 표시를 해제한다.
        const failedArr = state.failed_steps[phase] ?? [];
        const failed_steps = failedArr.includes(msg.step)
          ? { ...state.failed_steps, [phase]: failedArr.filter((s) => s !== msg.step) }
          : state.failed_steps;
        // 작성 단계: 섹션이 바뀌면 라이브 본문을 초기화한다.
        const writing =
          phase === "writing"
            ? { writing_draft: "", writing_section: msg.step }
            : { writing_draft: state.writing_draft, writing_section: state.writing_section };
        return {
          ...state,
          current_step: msg.step,
          eta_seconds: msg.eta_seconds ?? state.eta_seconds,
          failed_steps,
          ...writing,
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
      return {
        ...state,
        writing_section: state.writing_section ?? msg.section_id,
        writing_draft: appendStream(state.writing_draft, msg.delta, DRAFT_MAX_CHARS),
      };
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
