import { ws } from "msw";
import { getOrCreateRunner } from "@/api/mock/fixtures/scenarios/state";

const progressLink = ws.link(/^wss?:\/\/[^/]+\/ws\/projects\/[^/]+\/progress/);

export const wsHandlers = [
  progressLink.addEventListener("connection", ({ client }) => {
    const url = new URL(client.url);
    const match = url.pathname.match(/\/ws\/projects\/([^/]+)\/progress/);
    const projectId = match?.[1] ?? "unknown";
    const scenarioKey = url.searchParams.get("scenario") ?? "full";

    const runner = getOrCreateRunner(projectId, scenarioKey);

    const send = (data: string) => {
      try {
        client.send(data);
      } catch (e) {
        console.warn("[mock-ws] send failed", e);
      }
    };
    const unsubscribe = runner.subscribe({ send });

    client.addEventListener("close", () => {
      unsubscribe();
    });
  }),
];
