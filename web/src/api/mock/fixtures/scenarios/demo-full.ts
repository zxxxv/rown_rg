import { RICH_2_3, RICH_3_3 } from "@/api/mock/fixtures/section-content";
import type { ProgressMessage } from "@/api/ws-messages";
import {
  CHAPTER_SUMMARY,
  CONTRA,
  CRITIC_THOUGHT,
  OUTLINE,
  POLISH_NOTE,
  RESEARCH_KEYWORDS,
  SOURCE_EVAL,
} from "./_content";
import { draftTypewriter, sleep, typewriter } from "./_utils";

export async function* demoFullScenario(costStep = 1): AsyncGenerator<ProgressMessage> {
  let tokens = 0;
  let cost = 0;
  const bumpCost = (t: number, c: number): ProgressMessage => {
    tokens += t;
    cost += c;
    return { type: "cost", tokens_used: tokens, cost_usd: Number(cost.toFixed(2)) };
  };
  const nap = (ms: number) => sleep(ms / costStep);

  // ========== RESEARCH ==========
  yield { type: "phase", phase: "research", status: "started" };
  await nap(600);

  yield {
    type: "step",
    phase: "research",
    step: "검색어 생성",
    status: "started",
    eta_seconds: 5400,
  };
  await nap(400);
  yield* typewriter("research_keywords", RESEARCH_KEYWORDS, 26 / costStep);
  yield bumpCost(180_000, 6);
  yield { type: "step", phase: "research", step: "검색어 생성", status: "completed" };
  await nap(500);

  yield {
    type: "step",
    phase: "research",
    step: "자료 검색 (듀얼 트랙)",
    status: "started",
    eta_seconds: 4800,
  };
  await nap(3000);
  yield bumpCost(90_000, 3);
  yield { type: "step", phase: "research", step: "자료 검색 (듀얼 트랙)", status: "completed" };
  await nap(500);

  yield { type: "step", phase: "research", step: "자료 평가", status: "started" };
  await nap(900);
  yield* typewriter("research_keywords", SOURCE_EVAL, 24 / costStep);
  yield bumpCost(120_000, 4);
  yield { type: "step", phase: "research", step: "자료 평가", status: "completed" };
  yield { type: "phase", phase: "research", status: "completed" };

  // ========== CHECKPOINT #1 (자료 검토) ==========
  await nap(400);
  yield {
    type: "checkpoint",
    checkpoint_id: "cp_sources_review",
    level: 1,
    requires_user_decision: true,
  };
  await nap(3500);

  // ========== INDEXING ==========
  yield { type: "phase", phase: "indexing", status: "started" };
  await nap(500);

  yield {
    type: "step",
    phase: "indexing",
    step: "청크 분할",
    status: "started",
    eta_seconds: 4200,
  };
  await nap(2500);
  yield bumpCost(80_000, 1);
  yield { type: "step", phase: "indexing", step: "청크 분할", status: "completed" };
  await nap(300);

  yield { type: "step", phase: "indexing", step: "임베딩 생성", status: "started" };
  await nap(3500);
  yield bumpCost(150_000, 3);
  yield { type: "step", phase: "indexing", step: "임베딩 생성", status: "completed" };
  await nap(300);

  yield { type: "step", phase: "indexing", step: "벡터 색인 구축", status: "started" };
  await nap(2600);
  yield bumpCost(40_000, 1);
  yield { type: "step", phase: "indexing", step: "벡터 색인 구축", status: "completed" };
  yield { type: "phase", phase: "indexing", status: "completed" };

  // ========== WRITING ==========
  yield { type: "phase", phase: "writing", status: "started" };
  await nap(400);

  // Level 1 — 개요·목차
  yield {
    type: "step",
    phase: "writing",
    step: "개요·목차 생성",
    status: "started",
    eta_seconds: 3600,
  };
  await nap(400);
  yield* draftTypewriter("outline", OUTLINE, 26 / costStep);
  yield bumpCost(250_000, 12);
  yield { type: "step", phase: "writing", step: "개요·목차 생성", status: "completed" };
  await nap(400);

  // Level 2 — 챕터 요약 → Checkpoint #2
  yield {
    type: "step",
    phase: "writing",
    step: "챕터 요약 작성",
    status: "started",
    eta_seconds: 2400,
  };
  await nap(400);
  yield* draftTypewriter("chapter_summary", CHAPTER_SUMMARY, 22 / costStep);
  yield bumpCost(500_000, 24);
  yield { type: "step", phase: "writing", step: "챕터 요약 작성", status: "completed" };

  await nap(400);
  yield {
    type: "checkpoint",
    checkpoint_id: "cp_structure_review",
    level: 2,
    requires_user_decision: true,
  };
  await nap(3500);

  // Level 3 — 섹션 본문 상세 작성 (Preview 화면과 동일 본문 재활용)
  const SECTIONS: { id: string; title: string; body: string }[] = [
    { id: "2.3", title: "본문 작성 · 2.3 인구·고령화 영향", body: RICH_2_3 },
    { id: "3.3", title: "본문 작성 · 3.3 비용편익비 (B/C)", body: RICH_3_3 },
  ];
  for (const sec of SECTIONS) {
    yield {
      type: "step",
      phase: "writing",
      step: sec.title,
      status: "started",
      eta_seconds: 1800,
    };
    await nap(400);
    yield* draftTypewriter(sec.id, sec.body, 18 / costStep);
    yield bumpCost(750_000, 36);
    yield { type: "step", phase: "writing", step: sec.title, status: "completed" };
    await nap(300);
  }

  // Level 4 — 통합·교정
  yield { type: "step", phase: "writing", step: "통합·교정", status: "started", eta_seconds: 600 };
  await nap(400);
  yield* draftTypewriter("polish", POLISH_NOTE, 22 / costStep);
  yield bumpCost(300_000, 14);
  yield { type: "step", phase: "writing", step: "통합·교정", status: "completed" };
  yield { type: "phase", phase: "writing", status: "completed" };

  // ========== QA ==========
  yield { type: "phase", phase: "qa", status: "started" };
  await nap(400);

  // Fact-check
  yield { type: "step", phase: "qa", step: "Fact-check", status: "started" };
  await nap(2000);
  yield bumpCost(120_000, 3);
  yield { type: "step", phase: "qa", step: "Fact-check", status: "completed" };

  // Consistency — 모순 발견 → 실패 → 재시도 → 통과
  yield { type: "step", phase: "qa", step: "Consistency", status: "started" };
  await nap(800);
  yield* typewriter("contradiction_explain", CONTRA, 30 / costStep);
  yield { type: "step", phase: "qa", step: "Consistency", status: "failed" };
  await nap(1200);
  yield { type: "step", phase: "qa", step: "Consistency", status: "started" };
  await nap(1800);
  yield bumpCost(140_000, 4);
  yield { type: "step", phase: "qa", step: "Consistency", status: "completed" };

  // Style
  yield { type: "step", phase: "qa", step: "Style", status: "started" };
  await nap(2000);
  yield bumpCost(90_000, 2);
  yield { type: "step", phase: "qa", step: "Style", status: "completed" };

  // Critic
  yield { type: "step", phase: "qa", step: "Critic", status: "started" };
  await nap(900);
  yield* typewriter("critic_thinking", CRITIC_THOUGHT, 35 / costStep);
  yield bumpCost(160_000, 5);
  yield { type: "step", phase: "qa", step: "Critic", status: "completed" };
  yield { type: "phase", phase: "qa", status: "completed" };

  // ========== EXPORT ==========
  yield { type: "phase", phase: "export", status: "started" };
  await nap(600);

  yield { type: "step", phase: "export", step: "HWPX 변환", status: "started" };
  await nap(2000);
  yield bumpCost(60_000, 2);
  yield { type: "step", phase: "export", step: "HWPX 변환", status: "completed" };
  await nap(300);

  yield { type: "step", phase: "export", step: "PDF 변환", status: "started" };
  await nap(1500);
  yield { type: "step", phase: "export", step: "PDF 변환", status: "completed" };
  await nap(300);

  yield { type: "step", phase: "export", step: "Markdown 정리", status: "started" };
  await nap(1000);
  yield { type: "step", phase: "export", step: "Markdown 정리", status: "completed" };
  yield { type: "phase", phase: "export", status: "completed" };
}
