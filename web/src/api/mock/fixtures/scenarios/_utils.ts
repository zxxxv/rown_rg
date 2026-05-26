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
