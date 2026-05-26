import { useCallback, useEffect, useRef, useState } from "react";
import { type ProgressMessage, ProgressMessageSchema } from "@/api/ws-messages";

export type WebSocketStatus = "connecting" | "open" | "closed" | "error";

export interface UseWebSocketOptions {
  url: string;
  onMessage?: (msg: ProgressMessage) => void;
  enabled?: boolean;
}

export interface UseWebSocketReturn {
  status: WebSocketStatus;
  attempts: number;
  reconnect: () => void;
  disconnect: () => void;
}

const MAX_ATTEMPTS = 6;

function backoffMs(attempt: number): number {
  return Math.min(30_000, 1_000 * 2 ** attempt);
}

export function useWebSocket({
  url,
  onMessage,
  enabled = true,
}: UseWebSocketOptions): UseWebSocketReturn {
  const [status, setStatus] = useState<WebSocketStatus>(enabled ? "connecting" : "closed");
  const [attempts, setAttempts] = useState(0);

  const socketRef = useRef<WebSocket | null>(null);
  const timerRef = useRef<number | null>(null);
  const attemptsRef = useRef(0);
  const intentionalRef = useRef(false);
  const onMessageRef = useRef(onMessage);
  const urlRef = useRef(url);
  const connectRef = useRef<() => void>(() => {});

  useEffect(() => {
    onMessageRef.current = onMessage;
  }, [onMessage]);

  useEffect(() => {
    urlRef.current = url;
  }, [url]);

  const cleanup = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    const s = socketRef.current;
    if (s) {
      socketRef.current = null;
      s.onopen = null;
      s.onmessage = null;
      s.onclose = null;
      s.onerror = null;
      if (s.readyState === WebSocket.OPEN || s.readyState === WebSocket.CONNECTING) {
        s.close();
      }
    }
  }, []);

  const scheduleReconnect = useCallback(() => {
    if (intentionalRef.current) return;
    if (attemptsRef.current >= MAX_ATTEMPTS) return;
    const wait = backoffMs(attemptsRef.current);
    attemptsRef.current += 1;
    setAttempts(attemptsRef.current);
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      connectRef.current();
    }, wait);
  }, []);

  const connect = useCallback(() => {
    cleanup();
    intentionalRef.current = false;
    setStatus("connecting");

    let ws: WebSocket;
    try {
      ws = new WebSocket(urlRef.current);
    } catch (e) {
      console.warn("[useWebSocket] connection failed", e);
      setStatus("error");
      scheduleReconnect();
      return;
    }
    socketRef.current = ws;

    ws.onopen = () => {
      attemptsRef.current = 0;
      setAttempts(0);
      setStatus("open");
    };

    ws.onmessage = (event) => {
      let json: unknown;
      try {
        json = JSON.parse(typeof event.data === "string" ? event.data : "");
      } catch (e) {
        console.warn("[useWebSocket] non-JSON message", e);
        return;
      }
      const parsed = ProgressMessageSchema.safeParse(json);
      if (!parsed.success) {
        console.warn("[useWebSocket] invalid message", parsed.error.flatten());
        return;
      }
      onMessageRef.current?.(parsed.data);
    };

    ws.onerror = () => {
      setStatus("error");
    };

    ws.onclose = () => {
      socketRef.current = null;
      setStatus("closed");
      scheduleReconnect();
    };
  }, [cleanup, scheduleReconnect]);

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  const reconnect = useCallback(() => {
    intentionalRef.current = false;
    attemptsRef.current = 0;
    setAttempts(0);
    connect();
  }, [connect]);

  const disconnect = useCallback(() => {
    intentionalRef.current = true;
    cleanup();
    setStatus("closed");
  }, [cleanup]);

  useEffect(() => {
    if (!enabled) {
      intentionalRef.current = true;
      cleanup();
      setStatus("closed");
      return;
    }
    attemptsRef.current = 0;
    setAttempts(0);
    connect();
    return () => {
      intentionalRef.current = true;
      cleanup();
    };
  }, [enabled, connect, cleanup]);

  return { status, attempts, reconnect, disconnect };
}
