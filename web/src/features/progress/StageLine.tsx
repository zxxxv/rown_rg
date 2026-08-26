import { AlertTriangle, BadgeCheck, Loader2, PauseCircle } from "lucide-react";
import { useDrift } from "@/api/drift";
import type { ProgressSnapshot } from "@/api/progress";
import { useProjectSections } from "@/api/sections";
import type { Project } from "@/api/types";
import { cn } from "@/lib/utils";

/** 개요 상단 한 줄 - "지금 어디까지 왔고, 다음에 무엇을 하면 되나".
 *
 * 여러 화면을 오가며 고치다 보면 지금 상태가 헷갈린다는 지적에서 나왔다(2026-08-26).
 * 다만 단계 **번호**를 박지는 않는다: status는 '어디까지 했나'와 '지금 뭘 돌리나'를
 * 겸해서 거짓말을 할 수 있고(재개 오표시가 그 사례), 번호를 헤더로 승격시키면 그
 * 거짓말이 제일 먼저 읽힌다. 대신 **산출물에서 센 값**만 쓴다 - 절 수와 미반영 수는
 * 세면 나오는 사실이라 틀릴 수가 없다.
 */
export function StageLine({
  project,
  snapshot,
}: {
  project: Project;
  snapshot: ProgressSnapshot | undefined;
}) {
  const sections = useProjectSections(project.id);
  const drift = useDrift(project.id);

  const leaves = (sections.data?.tree ?? []).flatMap((c) => c.children);
  const done = leaves.filter((s) => s.status === "completed").length;
  const unreflected = drift.data?.sections.length ?? 0;
  const running = snapshot?.runner_alive ?? false;
  const gate = snapshot?.pending_gate;
  const finalized = Boolean(project.finalized_at);

  // 색은 상태를 한눈에 가르는 첫 신호다 - 글자만으로는 훑을 때 안 읽힌다(2026-08-26
  // 지적: "너무 작고 밋밋해서 잘 안 보여"). 도는 중=파랑, 기다림=주황, 확정=초록.
  let icon = <BadgeCheck className="h-4 w-4 text-fg-success" aria-hidden />;
  let tone = "border-border bg-bg-secondary/40 text-fg";
  let phase = "검토 중";
  let next = "";

  if (running) {
    icon = <Loader2 className="h-5 w-5 animate-spin text-fg-info" aria-hidden />;
    tone = "border-border-info bg-bg-info text-fg-info";
    phase = snapshot?.active_step ?? "진행 중";
    next = "끝나면 알려 드립니다";
  } else if (gate) {
    icon = <PauseCircle className="h-5 w-5 text-fg-warning" aria-hidden />;
    tone = "border-fg-warning/40 bg-bg-warning text-fg";
    phase = gate.gate === "design_brief" ? "설계 검토 대기" : "자료 검토 대기";
    next = "검토를 마치면 이어서 진행됩니다";
  } else if (project.status === "created") {
    phase = "시작 전";
    next = "자료 조사부터 시작합니다";
  } else if (finalized) {
    tone = "border-fg-success/40 bg-bg-success text-fg";
    phase = "최종 확정";
    next = unreflected > 0 ? "미반영 절이 있습니다" : "";
  } else if (unreflected > 0) {
    icon = <AlertTriangle className="h-5 w-5 text-fg-warning" aria-hidden />;
    tone = "border-fg-warning/40 bg-bg-warning text-fg";
    phase = "검토 중";
    next = `미반영 ${unreflected}개 절을 다시 쓰거나 그대로 두세요`;
  } else {
    next =
      leaves.length > 0 && done < leaves.length
        ? "빈 절이 남아 있습니다"
        : "확정하거나 계속 손보세요";
  }

  const facts: string[] = [];
  if (leaves.length > 0) facts.push(`본문 ${done}/${leaves.length}`);
  if (unreflected > 0) facts.push(`미반영 ${unreflected}`);

  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border px-4 py-2.5 text-base",
        tone,
      )}
    >
      {icon}
      <span className="text-lg font-semibold">{phase}</span>
      {facts.length > 0 ? (
        <span className="font-medium text-fg-secondary">{facts.join(" · ")}</span>
      ) : null}
      {next ? <span className="text-sm text-fg-tertiary">다음: {next}</span> : null}
    </div>
  );
}
