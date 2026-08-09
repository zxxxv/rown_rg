import type { ProgressMessage } from "@/api/ws-messages";
import { demoErrorScenario } from "./demo-error";
import { demoFullScenario } from "./demo-full";
import { demoQuickScenario } from "./demo-quick";

export type ScenarioKey = "full" | "quick" | "error";

export const SCENARIO_LABEL: Record<ScenarioKey, string> = {
  full: "데모 - 풀 시나리오 (약 2분)",
  quick: "데모 - 빠른 시나리오 (약 25초)",
  error: "데모 - 작성 중 오류 시나리오",
};

export function pickScenario(key: string): () => AsyncGenerator<ProgressMessage> {
  switch (key) {
    case "quick":
      return demoQuickScenario;
    case "error":
      return demoErrorScenario;
    default:
      return () => demoFullScenario(1);
  }
}
