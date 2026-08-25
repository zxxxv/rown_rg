import { CircleUser } from "lucide-react";
import { HelpTip } from "@/components/ui/help-tip";

// ─── 생성 뒤에 무슨 일이 일어나는가 ───
// 생성 화면은 "생성"까지만 말하고 그 뒤 몇 단계가 남는지 알려주지 않았다. 처음 쓰는
// 사람의 가장 큰 불안이 "누르면 무슨 일이 일어나지?"인데, 화면이 답하지 않았다
// (2026-08-25 전수 조사). 단계 이름은 진행 화면의 스테퍼(PipelineStepper)와 같은 말을
// 쓴다 - 여기서 본 단어가 그대로 진행 화면에 나와야 이어진다.

/** 사람이 확인해야 넘어가는 단계인가. 이게 이 안내의 핵심이다 - 눌렀다고 끝까지
 * 자동으로 가지 않는다는 사실을 미리 알아야 한다. */
const STEPS: { label: string; human?: boolean }[] = [
  { label: "설계 검토", human: true },
  { label: "자료 수집" },
  { label: "자료 검토", human: true },
  { label: "색인·리허설" },
  { label: "본문 작성" },
  { label: "조립·검증" },
  { label: "완성 검토", human: true },
];

export function RunFlowGuide() {
  const humanCount = STEPS.filter((s) => s.human).length;
  return (
    <div className="flex flex-col gap-2 rounded border border-border bg-bg p-4">
      <div className="flex items-center gap-1">
        <p className="text-xs font-medium text-fg-secondary">만들면 이렇게 진행됩니다</p>
        <HelpTip title="생성 뒤 진행 순서">
          <p>
            생성을 누르면 바로 시작합니다. 끝까지 자동으로 가지는 않고, 중간에{" "}
            <b>{humanCount}번 사람이 확인</b>합니다.
          </p>
          <p>
            <b>설계 검토</b>에서는 절마다 무엇을 어떻게 찾을지 계획을 보여 줍니다. 여기서 예상
            소요와 비용도 함께 나옵니다.
          </p>
          <p>
            <b>자료 검토</b>에서는 모아 온 자료를 확정하거나 제외합니다. 여기서 자료를 직접 올릴
            수도 있습니다.
          </p>
          <p>
            <b>완성 검토</b>에서는 쓰인 본문을 절 단위로 보고, 고칠 절만 다시 쓰게 할 수 있습니다.
          </p>
          <p>중간에 화면을 닫아도 계속 돌아갑니다. 알림을 켜 두면 끝났을 때 알려 줍니다.</p>
        </HelpTip>
      </div>
      <ol className="flex flex-col gap-1">
        {STEPS.map((step, i) => (
          <li key={step.label} className="flex items-center gap-2 text-[11px]">
            <span className="w-4 shrink-0 text-right font-mono text-fg-tertiary">{i + 1}</span>
            <span className={step.human ? "font-medium text-fg" : "text-fg-secondary"}>
              {step.label}
            </span>
            {step.human ? (
              <span className="flex items-center gap-0.5 text-accent">
                <CircleUser className="h-3 w-3" aria-hidden />
                <span className="text-[10px]">내 확인</span>
              </span>
            ) : null}
          </li>
        ))}
      </ol>
      <p className="text-[11px] text-fg-tertiary">
        예상 소요와 비용은 1번 설계 검토에서 알려 줍니다.
      </p>
    </div>
  );
}
