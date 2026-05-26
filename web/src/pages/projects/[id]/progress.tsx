import { ArrowLeft, FileText, Wifi, WifiOff } from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { type CheckpointDecision, useDecideCheckpoint } from "@/api/checkpoints";
import { ApiError } from "@/api/client";
import { useProgressSnapshot } from "@/api/progress";
import { useProject } from "@/api/projects";
import type { PhaseName } from "@/api/ws-messages";
import {
  type CheckpointDecision as CheckpointButtonDecision,
  ReviewCheckpoint,
} from "@/components/data-display/ReviewCheckpoint";
import { StatusDot, type StatusKind } from "@/components/data-display/StatusDot";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CostTracker } from "@/features/progress/CostTracker";
import { EtaIndicator } from "@/features/progress/EtaIndicator";
import { PhaseTracker } from "@/features/progress/PhaseTracker";
import { QAStatusList } from "@/features/progress/QAStatusList";
import { StepStream } from "@/features/progress/StepStream";
import { useProgressState } from "@/features/progress/useProgressState";
import { estimate } from "@/features/project-config/estimator";
import { useAuth } from "@/hooks/useAuth";
import { useWebSocket, type WebSocketStatus } from "@/hooks/useWebSocket";

const PHASE_DETAIL_TITLE: Record<PhaseName, string> = {
  research: "자료조사 — 검색어 생성 / 자료 평가",
  indexing: "인덱싱 — 청크 분할 / 임베딩 생성",
  writing: "작성 — Level 1 → 4",
  qa: "검증 — Fact / Consistency / Style / Critic",
  export: "출력 — HWPX / PDF / Markdown",
};

function buildWsUrl(projectId: string, scenario: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/projects/${projectId}/progress?scenario=${scenario}`;
}

export default function ProgressPage() {
  const { id: projectId = "demo" } = useParams<{ id: string }>();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { user, logout } = useAuth();

  const isDemo = params.get("demo") === "1";
  const scenario = params.get("scenario") ?? (isDemo ? "quick" : "full");

  const startedAtRef = useRef(Date.now());
  const projectQuery = useProject(projectId);
  const project = projectQuery.data;
  const { state, onWsMessage, applySnapshot, clearCheckpoint } = useProgressState();
  const snapshotQuery = useProgressSnapshot(projectId, true);

  const estimated = useMemo(
    () =>
      project
        ? estimate(project.config)
        : { estimatedHours: 0, estimatedTokens: 2_100_000, estimatedCostUsd: 280 },
    [project],
  );

  useEffect(() => {
    if (snapshotQuery.data) applySnapshot(snapshotQuery.data);
  }, [snapshotQuery.data, applySnapshot]);

  const wsUrl = useMemo(() => buildWsUrl(projectId, scenario), [projectId, scenario]);
  const {
    status: wsStatus,
    attempts,
    reconnect,
  } = useWebSocket({
    url: wsUrl,
    onMessage: onWsMessage,
  });

  // Toast on connection drops / reconnect
  const prevStatusRef = useRef<WebSocketStatus>("connecting");
  useEffect(() => {
    const prev = prevStatusRef.current;
    if (prev === "open" && (wsStatus === "closed" || wsStatus === "error")) {
      toast.warning("연결이 끊어졌습니다", { description: "자동 재연결을 시도합니다." });
      void snapshotQuery.refetch();
    } else if (prev !== "open" && wsStatus === "open" && attempts === 0) {
      // first connect, no toast
    } else if ((prev === "closed" || prev === "error") && wsStatus === "open" && attempts > 0) {
      toast.success("재연결됨", { description: "누락된 상태를 동기화합니다." });
      void snapshotQuery.refetch();
    }
    prevStatusRef.current = wsStatus;
  }, [wsStatus, attempts, snapshotQuery]);

  const decide = useDecideCheckpoint();
  const onDecide = (decision: CheckpointDecision) => {
    if (!state.checkpoint_id) return;
    const cid = state.checkpoint_id;
    decide.mutate(
      { projectId, checkpointId: cid, decision },
      {
        onSuccess: () => {
          toast.success("결정이 적용됐습니다.");
          clearCheckpoint();
        },
        onError: (err) => {
          const msg = err instanceof ApiError ? err.message : "결정 적용 실패";
          toast.error("결정 적용 실패", { description: msg });
        },
      },
    );
  };

  return (
    <AppShell
      user={user ? { name: user.name, role: user.role } : null}
      onLogout={() => void logout()}
      tokenUsage={{ used: state.tokens_used, limit: 5_000_000 }}
    >
      <div className="flex flex-col gap-6">
        <header className="flex flex-col gap-2">
          <Button
            variant="ghost"
            size="sm"
            className="w-fit text-fg-secondary"
            onClick={() => navigate(`/projects/${projectId}/overview`)}
          >
            <ArrowLeft className="mr-1 h-4 w-4" />
            프로젝트 개요
          </Button>
          <div className="flex items-start justify-between gap-4">
            <div>
              <h1 className="text-3xl font-semibold text-fg">진행 상황</h1>
              <p className="mt-1 text-sm text-fg-secondary">
                프로젝트 <span className="font-mono">{projectId}</span> · 시나리오{" "}
                <span className="font-mono">{scenario}</span>
              </p>
            </div>
            {isDemo ? (
              <Badge variant="default" className="self-start">
                발표 모드
              </Badge>
            ) : null}
          </div>
        </header>

        {state.checkpoint_id ? (
          <ReviewCheckpoint
            number={(state.checkpoint_level ?? 1) as 1 | 2}
            title={state.checkpoint_level === 2 ? "구조 검토 — Level 2 완료" : "자료 검토 요약"}
            description="AI가 다음 단계로 진행하기 전 사용자 결정을 기다리는 중입니다."
            decisions={CHECKPOINT_BUTTONS.map((b) => ({
              label: b.label,
              intent: b.intent,
              onClick: () => onDecide(b.decision),
              disabled: decide.isPending,
            }))}
          >
            <div className="flex items-start gap-2 text-sm text-fg-secondary">
              <FileText className="mt-0.5 h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
              <p>
                여기에 검토 대상 본문 요약이 표시됩니다 (현재 단계: 채택된 자료의 핵심 인용구 목록과
                구성 초안). 결정을 내리면 다음 단계로 자동 진행됩니다.
              </p>
            </div>
          </ReviewCheckpoint>
        ) : (
          <>
            <PhaseTracker
              activePhase={state.active_phase}
              phaseStatus={state.phase_status}
              completedPhases={state.completed_phases}
              failedPhases={
                Object.keys(state.failed_steps).length > 0
                  ? new Set(Object.keys(state.failed_steps) as PhaseName[])
                  : undefined
              }
            />

            <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
              <section className="flex flex-col gap-4">
                <header className="flex items-center justify-between">
                  <h2 className="text-xl font-semibold text-fg">
                    {PHASE_DETAIL_TITLE[state.active_phase]}
                  </h2>
                  {state.current_step ? (
                    <Badge variant="secondary" className="font-mono">
                      {state.current_step}
                    </Badge>
                  ) : null}
                </header>

                <PhaseDetail state={state} />

                {state.error ? (
                  <div
                    role="alert"
                    className="rounded border border-fg-danger/30 bg-bg-danger p-4 text-sm"
                  >
                    <p className="font-medium text-fg-danger">오류 — {state.error.code}</p>
                    <p className="text-fg-secondary">{state.error.message}</p>
                  </div>
                ) : null}
              </section>

              <aside className="flex flex-col gap-4 lg:sticky lg:top-6 lg:self-start">
                <CostTracker
                  tokensUsed={state.tokens_used}
                  tokensExpected={estimated.estimatedTokens}
                  costUsed={state.cost_usd}
                  costExpected={estimated.estimatedCostUsd}
                />
                <EtaIndicator startedAt={startedAtRef.current} etaSeconds={state.eta_seconds} />
                <ConnectionPanel status={wsStatus} attempts={attempts} onReconnect={reconnect} />
              </aside>
            </div>
          </>
        )}
      </div>
    </AppShell>
  );
}

const CHECKPOINT_BUTTONS: {
  label: string;
  intent: CheckpointButtonDecision["intent"];
  decision: CheckpointDecision;
}[] = [
  { label: "승인", intent: "primary", decision: "approve" },
  { label: "수정 요청", intent: "secondary", decision: "request_changes" },
  { label: "더 깊게", intent: "secondary", decision: "deeper" },
  { label: "여기서 멈춤", intent: "danger", decision: "halt" },
];

function PhaseDetail({ state }: { state: ReturnType<typeof useProgressState>["state"] }) {
  switch (state.active_phase) {
    case "research":
      return <StepStream channel="research_keywords" text={state.streams.research_keywords} />;
    case "indexing":
      return (
        <div className="grid grid-cols-2 gap-3">
          <Counter label="청크 분할" status={status("indexing", "청크 분할", state)} />
          <Counter label="임베딩 생성" status={status("indexing", "임베딩 생성", state)} />
        </div>
      );
    case "writing":
      return (
        <div className="flex flex-col gap-2">
          {[1, 2, 3, 4].map((lv) => (
            <Counter
              key={lv}
              label={`Level ${lv}`}
              status={status("writing", `Level ${lv}`, state)}
            />
          ))}
        </div>
      );
    case "qa":
      return (
        <QAStatusList
          currentStep={state.current_step}
          completedSteps={state.completed_steps.qa ?? []}
          failedSteps={state.failed_steps.qa ?? []}
          criticStream={state.streams.critic_thinking}
        />
      );
    case "export":
      return (
        <div className="rounded border border-border bg-bg p-4 text-sm text-fg-secondary">
          HWPX 변환 후 다운로드 페이지로 이동할 수 있습니다.
        </div>
      );
    default:
      return null;
  }
}

type DetailStatus = "pending" | "running" | "completed" | "failed";

function status(
  phase: string,
  step: string,
  state: ReturnType<typeof useProgressState>["state"],
): DetailStatus {
  const failed = state.failed_steps[phase] ?? [];
  if (failed.includes(step)) return "failed";
  const completed = state.completed_steps[phase] ?? [];
  if (completed.includes(step)) return "completed";
  if (state.current_step === step && state.active_phase === phase) return "running";
  return "pending";
}

const COUNTER_KIND: Record<DetailStatus, StatusKind> = {
  pending: "tertiary",
  running: "info",
  completed: "success",
  failed: "danger",
};

const COUNTER_LABEL: Record<DetailStatus, string> = {
  pending: "대기",
  running: "진행 중",
  completed: "완료",
  failed: "실패",
};

function Counter({ label, status: s }: { label: string; status: DetailStatus }) {
  return (
    <div className="flex items-center justify-between rounded border border-border bg-bg p-3">
      <span className="text-sm font-medium text-fg">{label}</span>
      <StatusDot kind={COUNTER_KIND[s]} label={COUNTER_LABEL[s]} />
    </div>
  );
}

const WS_KIND: Record<WebSocketStatus, StatusKind> = {
  connecting: "warning",
  open: "success",
  closed: "tertiary",
  error: "danger",
};

const WS_LABEL: Record<WebSocketStatus, string> = {
  connecting: "연결 중",
  open: "실시간 연결됨",
  closed: "끊김",
  error: "오류",
};

function ConnectionPanel({
  status: s,
  attempts,
  onReconnect,
}: {
  status: WebSocketStatus;
  attempts: number;
  onReconnect: () => void;
}) {
  return (
    <div className="flex items-center justify-between rounded border border-border bg-bg p-3">
      <div className="flex items-center gap-2">
        {s === "open" ? (
          <Wifi className="h-4 w-4 text-fg-success" aria-hidden />
        ) : (
          <WifiOff className="h-4 w-4 text-fg-tertiary" aria-hidden />
        )}
        <StatusDot kind={WS_KIND[s]} label={WS_LABEL[s]} />
      </div>
      {attempts > 0 && s !== "open" ? (
        <Button variant="ghost" size="sm" onClick={onReconnect}>
          재시도 {attempts}/6
        </Button>
      ) : null}
    </div>
  );
}
