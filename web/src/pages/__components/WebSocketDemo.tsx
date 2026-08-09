import { useState } from "react";
import { SCENARIO_LABEL, type ScenarioKey } from "@/api/mock/fixtures/scenarios";
import type { ProgressMessage } from "@/api/ws-messages";
import { StatusDot, type StatusKind } from "@/components/data-display/StatusDot";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useWebSocket, type WebSocketStatus } from "@/hooks/useWebSocket";

const STATUS_KIND: Record<WebSocketStatus, StatusKind> = {
  connecting: "warning",
  open: "success",
  closed: "tertiary",
  error: "danger",
};

const STATUS_LABEL: Record<WebSocketStatus, string> = {
  connecting: "연결 중",
  open: "연결됨",
  closed: "끊김",
  error: "오류",
};

const SCENARIO_KEYS = Object.keys(SCENARIO_LABEL) as ScenarioKey[];

function buildWsUrl(scenario: ScenarioKey): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws/projects/demo/progress?scenario=${scenario}`;
}

export function WebSocketDemo() {
  const [scenario, setScenario] = useState<ScenarioKey>("quick");
  const [enabled, setEnabled] = useState(false);
  const [messages, setMessages] = useState<{ id: number; msg: ProgressMessage }[]>([]);

  const url = buildWsUrl(scenario);
  const { status, attempts, reconnect, disconnect } = useWebSocket({
    url,
    enabled,
    onMessage: (msg) =>
      setMessages((prev) => {
        const id = (prev[prev.length - 1]?.id ?? 0) + 1;
        const next = [...prev, { id, msg }];
        return next.length > 50 ? next.slice(next.length - 50) : next;
      }),
  });

  const startScenario = (next: ScenarioKey) => {
    setScenario(next);
    setMessages([]);
    setEnabled(true);
  };

  return (
    <div className="flex flex-col gap-3 rounded border border-border bg-bg p-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <StatusDot kind={STATUS_KIND[status]} label={STATUS_LABEL[status]} />
          {attempts > 0 ? (
            <span className="font-mono text-xs text-fg-tertiary">재시도 {attempts}/6</span>
          ) : null}
        </div>
        <span className="font-mono text-xs text-fg-tertiary">{url}</span>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Select value={scenario} onValueChange={(v) => setScenario(v as ScenarioKey)}>
          <SelectTrigger className="w-64">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SCENARIO_KEYS.map((k) => (
              <SelectItem key={k} value={k}>
                {SCENARIO_LABEL[k]}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button onClick={() => startScenario(scenario)}>시나리오 재생</Button>
        <Button variant="outline" onClick={() => reconnect()}>
          수동 reconnect
        </Button>
        <Button variant="ghost" onClick={() => disconnect()}>
          연결 끊기
        </Button>
        <span className="ml-auto font-mono text-xs text-fg-tertiary">
          메시지 {messages.length}/50
        </span>
      </div>

      <ScrollArea className="h-64 rounded border border-border bg-bg-secondary p-2">
        {messages.length === 0 ? (
          <p className="p-4 text-center text-xs text-fg-tertiary">
            시나리오를 재생하면 메시지가 여기 표시됩니다.
          </p>
        ) : (
          <ol className="flex flex-col gap-1">
            {messages.map(({ id, msg }) => (
              <li
                key={id}
                className="break-all rounded bg-bg px-2 py-1 font-mono text-[11px] text-fg-secondary"
              >
                {JSON.stringify(msg)}
              </li>
            ))}
          </ol>
        )}
      </ScrollArea>
    </div>
  );
}
