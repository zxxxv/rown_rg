import type { ProgressMessage } from "@/api/ws-messages";

export function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

export async function* typewriter(
  channel: "critic_thinking" | "research_keywords" | "contradiction_explain",
  text: string,
  perCharMs: number,
): AsyncGenerator<ProgressMessage> {
  const tokens = text.split(/(?<=[\s,.·])/);
  for (const t of tokens) {
    if (!t) continue;
    yield { type: "stream", channel, delta: t };
    await sleep(perCharMs * t.length);
  }
}

// 작성 단계의 본문 라이브 스트리밍 (fake_stream 채널, section_id별 본문 누적).
export async function* draftTypewriter(
  sectionId: string,
  text: string,
  perCharMs: number,
): AsyncGenerator<ProgressMessage> {
  // 본문은 길어서 줄바꿈 단위로도 분할해 자연스러운 타이핑 흐름을 만든다.
  const tokens = text.split(/(?<=[\s,.·\n])/);
  for (const t of tokens) {
    if (!t) continue;
    yield { type: "fake_stream", section_id: sectionId, delta: t };
    await sleep(perCharMs * t.length);
  }
}
