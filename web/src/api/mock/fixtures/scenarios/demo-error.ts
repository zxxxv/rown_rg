import type { ProgressMessage } from "@/api/ws-messages";
import { sleep } from "./_utils";

export async function* demoErrorScenario(): AsyncGenerator<ProgressMessage> {
  yield { type: "phase", phase: "research", status: "started" };
  await sleep(500);
  yield { type: "step", phase: "research", step: "검색어 생성", status: "completed" };
  yield { type: "step", phase: "research", step: "자료 평가", status: "completed" };
  yield { type: "phase", phase: "research", status: "completed" };

  yield { type: "phase", phase: "indexing", status: "started" };
  await sleep(800);
  yield { type: "step", phase: "indexing", step: "청크 분할", status: "completed" };
  yield { type: "step", phase: "indexing", step: "임베딩 생성", status: "completed" };
  yield { type: "phase", phase: "indexing", status: "completed" };

  yield { type: "phase", phase: "writing", status: "started" };
  await sleep(700);
  yield { type: "step", phase: "writing", step: "Level 1", status: "completed" };
  yield { type: "step", phase: "writing", step: "Level 2", status: "started" };
  await sleep(900);
  yield { type: "step", phase: "writing", step: "Level 2", status: "failed" };
  yield {
    type: "error",
    code: "llm_timeout",
    message: "Level 2 작성 중 LLM 응답이 시간 초과됐습니다. 잠시 후 자동 재시도됩니다.",
  };
}
