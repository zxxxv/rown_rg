import type { ProgressMessage } from "@/api/ws-messages";
import { sleep, typewriter } from "./_utils";

const CRITIC_THOUGHT =
  "이 섹션은 인구 고령화율 추세를 근거로 정책 효과를 추정하고 있다. 자료 A의 19.2% 수치와 자료 B의 17.1% 수치가 충돌한다. 자료 A는 통계청 2024년 표본 집계 결과로 신뢰도가 높다. 자료 B는 2022년 추정치를 사용했다. 추가 검증이 필요하다.";

const RESEARCH_KEYWORDS =
  "광역교통망, GTX, 통근 시간, 비용편익, 예비타당성, 인구 고령화, 수도권 집중도, 지방소멸";

export async function* demoFullScenario(costStep = 1): AsyncGenerator<ProgressMessage> {
  let tokens = 0;
  let cost = 0;
  const bumpCost = (t: number, c: number): ProgressMessage => {
    tokens += t;
    cost += c;
    return { type: "cost", tokens_used: tokens, cost_usd: Number(cost.toFixed(2)) };
  };

  // RESEARCH
  yield { type: "phase", phase: "research", status: "started" };
  await sleep(600 / costStep);
  yield {
    type: "step",
    phase: "research",
    step: "검색어 생성",
    status: "started",
    eta_seconds: 5400,
  };
  await sleep(400 / costStep);
  yield* typewriter("research_keywords", RESEARCH_KEYWORDS, 25 / costStep);
  yield { type: "step", phase: "research", step: "검색어 생성", status: "completed" };
  yield bumpCost(180_000, 6);
  await sleep(500 / costStep);
  yield { type: "step", phase: "research", step: "자료 평가", status: "started" };
  await sleep(1500 / costStep);
  yield bumpCost(120_000, 4);
  yield { type: "step", phase: "research", step: "자료 평가", status: "completed" };
  yield { type: "phase", phase: "research", status: "completed" };

  // CHECKPOINT #1 (자료 검토)
  await sleep(400 / costStep);
  yield {
    type: "checkpoint",
    checkpoint_id: "cp_sources_review",
    level: 1,
    requires_user_decision: true,
  };
  await sleep(3000 / costStep);

  // INDEXING
  yield { type: "phase", phase: "indexing", status: "started" };
  await sleep(500 / costStep);
  yield {
    type: "step",
    phase: "indexing",
    step: "청크 분할",
    status: "started",
    eta_seconds: 4200,
  };
  await sleep(1200 / costStep);
  yield bumpCost(80_000, 1);
  yield { type: "step", phase: "indexing", step: "청크 분할", status: "completed" };
  yield { type: "step", phase: "indexing", step: "임베딩 생성", status: "started" };
  await sleep(2200 / costStep);
  yield bumpCost(150_000, 3);
  yield { type: "step", phase: "indexing", step: "임베딩 생성", status: "completed" };
  yield { type: "phase", phase: "indexing", status: "completed" };

  // WRITING
  yield { type: "phase", phase: "writing", status: "started" };
  await sleep(400 / costStep);
  for (let lv = 1; lv <= 4; lv++) {
    yield {
      type: "step",
      phase: "writing",
      step: `Level ${lv}`,
      status: "started",
      eta_seconds: 3600 / lv,
    };
    await sleep(1200 / costStep);
    yield bumpCost(250_000 * lv, 12 * lv);
    yield { type: "step", phase: "writing", step: `Level ${lv}`, status: "completed" };

    if (lv === 2) {
      // Checkpoint #2 (구조 검토)
      yield {
        type: "checkpoint",
        checkpoint_id: "cp_structure_review",
        level: 2,
        requires_user_decision: true,
      };
      await sleep(2500 / costStep);
    }
  }
  yield { type: "phase", phase: "writing", status: "completed" };

  // QA
  yield { type: "phase", phase: "qa", status: "started" };
  await sleep(400 / costStep);
  for (const step of ["Fact-check", "Consistency", "Style", "Critic"]) {
    yield { type: "step", phase: "qa", step, status: "started" };
    await sleep(800 / costStep);
    if (step === "Critic") {
      yield* typewriter("critic_thinking", CRITIC_THOUGHT, 35 / costStep);
    }
    yield bumpCost(120_000, 3);
    yield { type: "step", phase: "qa", step, status: "completed" };
  }
  yield { type: "phase", phase: "qa", status: "completed" };

  // EXPORT
  yield { type: "phase", phase: "export", status: "started" };
  await sleep(500 / costStep);
  yield { type: "step", phase: "export", step: "HWPX 변환", status: "started" };
  await sleep(900 / costStep);
  yield { type: "step", phase: "export", step: "HWPX 변환", status: "completed" };
  yield { type: "phase", phase: "export", status: "completed" };
}
