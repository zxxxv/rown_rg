import { ArrowRight, Ban, Check, CircleDashed, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import type { ProgressSnapshot } from "@/api/progress";
import { useCancelProject } from "@/api/projects";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

// 파이프라인 순서 - 사람 결정이 필요한 단계(자료 검토)는 도달 시 강조되고 해당
// 작업 화면으로 가는 버튼이 붙는다. 본문 검토(QA) 게이트는 제거됨(2026-08-07) -
// 작성이 끝나면 곧장 조립·검증되고, 검토·편집은 보고서 화면에서 사후에 한다.
const STEPS = [
  { key: "collect", label: "자료 수집" },
  { key: "review", label: "자료 검토 · 확정" },
  { key: "index", label: "색인 (임베딩)" },
  { key: "write", label: "본문 작성" },
  { key: "assemble", label: "조립 · PM 검증" },
  { key: "done", label: "완성" },
] as const;

type StepPhase = "done" | "active" | "action" | "pending";

interface StepView {
  label: string;
  phase: StepPhase;
  actionLabel?: string;
  actionTo?: string;
}

/** 스냅샷(status + pending gate) → 스테퍼 뷰 상태. */
function deriveSteps(projectId: string, snapshot: ProgressSnapshot | undefined): StepView[] {
  const status = snapshot?.status ?? "created";
  const gate = snapshot?.pending_gate?.gate ?? null;

  // 현재 위치(index)와 그 단계의 표현. 게이트 대기가 status보다 우선한다 -
  // status는 척추 위치라 게이트 대기 중에도 직전 단계 값에 머문다.
  let current: number;
  let currentPhase: StepPhase = "active";
  let actionLabel: string | undefined;
  let actionTo: string | undefined;

  if (gate === "source_pool") {
    current = 1;
    currentPhase = "action";
    actionLabel = "자료 검토로 이동";
    actionTo = `/projects/${projectId}/sources`;
  } else if (gate === "qa_select") {
    // 레거시 - 게이트 제거 전 백엔드로 작성돼 아직 pending인 프로젝트만 도달한다.
    current = 4;
    currentPhase = "action";
    actionLabel = "본문 검토로 이동";
    actionTo = `/projects/${projectId}/preview`;
  } else {
    switch (status) {
      case "researching":
        current = 0;
        break;
      case "indexing":
        current = 2;
        break;
      case "writing":
        current = 3;
        break;
      case "reviewing":
        current = 4;
        break;
      case "completed":
      case "archived":
        current = 6; // 전부 완료
        break;
      default: // created·cancelled - 아직 진입 전(취소는 카드 하단에 별도 표기)
        current = -1;
    }
  }

  return STEPS.map((step, i) => {
    if (i < current) return { label: step.label, phase: "done" as const };
    if (i === current) return { label: step.label, phase: currentPhase, actionLabel, actionTo };
    return { label: step.label, phase: "pending" as const };
  });
}

function formatDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** 생성 시간 - 사람 검토 대기·중단 구간을 뺀 '실제로 돌던 시간'.

서버가 LLM 호출 간격에서 합산해 내려주고(active_seconds), 실행 중일 때만 마지막
활동 이후 경과를 더해 초 단위로 흐르게 한다. 게이트에서 멈추면 값이 그대로 선다
(2026-08-09: 게이트 9시간이 경과에 섞여 22시간으로 보였다). 벽시계 총경과는
부차 정보라 괄호로 함께 보여준다. */
function ElapsedRow({ snapshot }: { snapshot: ProgressSnapshot }) {
  const [now, setNow] = useState(() => Date.now());
  const ended = ["completed", "archived", "cancelled"].includes(snapshot.status);
  const waiting = Boolean(snapshot.pending_gate);
  const live = !ended && !waiting;
  useEffect(() => {
    if (!live) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [live]);

  const base = snapshot.active_seconds;
  if (base === undefined || !snapshot.started_at) return null;
  // 마지막 활동 이후 10분 넘게 조용하면 서버와 같은 기준으로 '멈춤'으로 보고 더하지 않는다.
  const sinceLast = snapshot.last_activity_at
    ? (now - Date.parse(snapshot.last_activity_at)) / 1000
    : 0;
  const seconds = base + (live && sinceLast > 0 && sinceLast <= 600 ? sinceLast : 0);
  const wallEnd = ended && snapshot.last_activity_at ? Date.parse(snapshot.last_activity_at) : now;
  const wall = (wallEnd - Date.parse(snapshot.started_at)) / 1000;

  return (
    <div className="flex items-center justify-between border-t border-border pt-3 text-xs">
      <span className="text-fg-tertiary">생성 시간{waiting ? " (검토 대기 중 멈춤)" : ""}</span>
      <span className="font-mono text-fg">
        {formatDuration(seconds * 1000)}
        <span className="ml-1 text-fg-tertiary">/ 총 {formatDuration(wall * 1000)}</span>
      </span>
    </div>
  );
}

export interface PipelineStepperProps {
  projectId: string;
  snapshot: ProgressSnapshot | undefined;
}

/** 개요 우측의 파이프라인 스테퍼 - 어디까지 왔는지, 다음에 사람이 뭘 해야 하는지. */
export function PipelineStepper({ projectId, snapshot }: PipelineStepperProps) {
  const navigate = useNavigate();
  const steps = deriveSteps(projectId, snapshot);
  const status = snapshot?.status ?? "created";
  const isActive = ["researching", "indexing", "writing", "reviewing"].includes(status);
  const cancelled = status === "cancelled";

  const cancel = useCancelProject();
  const [confirmCancel, setConfirmCancel] = useState(false);
  const onCancel = async () => {
    try {
      await cancel.mutateAsync(projectId);
      toast.success("실행을 취소했습니다.", {
        description: "이미 나간 요청이 정리될 때까지 잠시 걸릴 수 있습니다.",
      });
      setConfirmCancel(false);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "취소에 실패했습니다.";
      toast.error("취소 실패", { description: msg });
      setConfirmCancel(false);
    }
  };

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">진행 단계</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <ol className="flex flex-col gap-2.5">
          {steps.map((step) => (
            <li key={step.label} className="flex flex-col gap-1.5">
              <div className="flex items-center gap-2">
                {step.phase === "done" ? (
                  <Check className="h-4 w-4 shrink-0 text-fg-success" aria-hidden />
                ) : step.phase === "active" ? (
                  <Loader2 className="h-4 w-4 shrink-0 animate-spin text-accent" aria-hidden />
                ) : step.phase === "action" ? (
                  <ArrowRight className="h-4 w-4 shrink-0 text-fg-warning" aria-hidden />
                ) : (
                  <CircleDashed className="h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
                )}
                <span
                  className={cn(
                    "text-sm",
                    step.phase === "pending" && "text-fg-tertiary",
                    step.phase === "done" && "text-fg-secondary",
                    step.phase === "active" && "font-medium text-fg",
                    step.phase === "action" && "font-medium text-fg",
                  )}
                >
                  {step.label}
                </span>
                {step.phase === "action" ? (
                  <span className="ml-auto rounded-sm border border-fg-warning/40 bg-bg-warning px-1.5 py-0.5 text-[10px] font-medium text-fg-warning">
                    결정 필요
                  </span>
                ) : null}
              </div>
              {/* 실행 중 세부 단계 - 색인·RAPTOR처럼 수 분짜리 단계의 내부 진행
                  (예: "청킹·임베딩 5/17", "배경 요약 1층 · 40/152") */}
              {step.phase === "active" && snapshot?.active_step ? (
                <p className="ml-6 text-xs text-fg-tertiary">{snapshot.active_step}</p>
              ) : null}
              {step.phase === "action" && step.actionTo ? (
                <Button
                  size="sm"
                  className="ml-6 w-fit"
                  onClick={() => navigate(step.actionTo ?? "")}
                >
                  {step.actionLabel}
                  <ArrowRight className="ml-1 h-3.5 w-3.5" />
                </Button>
              ) : null}
            </li>
          ))}
        </ol>

        {snapshot?.queue_position ? (
          <p className="flex items-center gap-1.5 rounded border border-fg-info/30 bg-bg-info px-2.5 py-2 text-xs text-fg-secondary">
            <Loader2 className="h-3.5 w-3.5 animate-spin text-fg-info" aria-hidden />
            실행 대기열 {snapshot.queue_position}번째 - 앞선 실행이 끝나면 자동으로 시작됩니다.
          </p>
        ) : null}

        {cancelled ? (
          <p className="flex items-center gap-1.5 rounded border border-dashed border-border bg-bg-secondary px-2.5 py-2 text-xs text-fg-tertiary">
            <Ban className="h-3.5 w-3.5" aria-hidden />
            사용자가 실행을 취소했습니다 - 다시 시작하거나 삭제할 수 있습니다.
          </p>
        ) : null}

        {snapshot ? <ElapsedRow snapshot={snapshot} /> : null}

        {isActive ? (
          <>
            <Button
              variant="outline"
              size="sm"
              className="w-full text-fg-danger"
              onClick={() => setConfirmCancel(true)}
              disabled={cancel.isPending}
            >
              <Ban className="mr-1 h-3.5 w-3.5" />
              실행 취소
            </Button>
            <Dialog open={confirmCancel} onOpenChange={setConfirmCancel}>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>실행을 취소할까요?</DialogTitle>
                  <DialogDescription>
                    진행 중인 자동 실행이 중단됩니다. 이미 수집된 자료는 유지되며, 나중에 자료
                    조사부터 다시 시작할 수 있습니다.
                  </DialogDescription>
                </DialogHeader>
                <DialogFooter>
                  <Button variant="outline" onClick={() => setConfirmCancel(false)}>
                    계속 진행
                  </Button>
                  <Button
                    variant="destructive"
                    onClick={() => void onCancel()}
                    disabled={cancel.isPending}
                  >
                    {cancel.isPending ? "취소 중…" : "실행 취소"}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}
