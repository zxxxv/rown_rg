import ky, { HTTPError, type Options } from "ky";
import { toast } from "sonner";
import { env } from "@/env";

export class ApiError extends Error {
  readonly code: string;
  readonly status?: number;
  readonly details?: Record<string, unknown>;

  constructor(code: string, message: string, status?: number, details?: Record<string, unknown>) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

type Handler = () => void;
let onUnauthorized: Handler | null = null;
let onForbidden: Handler | null = null;

export function setUnauthorizedHandler(fn: Handler | null) {
  onUnauthorized = fn;
}

export function setForbiddenHandler(fn: Handler | null) {
  onForbidden = fn;
}

const AUTH_PATHS = ["/auth/login", "/auth/me", "/auth/logout", "/auth/refresh"];

function resolvePrefixUrl(raw: string): string {
  if (/^https?:\/\//i.test(raw)) return raw;
  if (typeof window !== "undefined") {
    return new URL(raw, window.location.origin).toString();
  }
  return raw;
}

const baseClient = ky.create({
  prefix: resolvePrefixUrl(env.VITE_API_BASE_URL),
  credentials: "include",
  retry: { limit: 1 },
  timeout: 30_000,
  hooks: {
    afterResponse: [
      async ({ request, response, retryCount }) => {
        const url = new URL(request.url);
        const suppressAuth = AUTH_PATHS.some((p) => url.pathname.endsWith(p));

        if (response.status === 401 && !suppressAuth) {
          // 토큰 만료 추정 → 1회 자동 갱신 후 원요청 재시도. 갱신 실패 시 로그아웃 처리.
          if (retryCount < 1 && (await refreshSession())) {
            return ky.retry();
          }
          onUnauthorized?.();
        } else if (response.status === 403 && !suppressAuth) {
          onForbidden?.();
        } else if (response.status >= 500) {
          toast.error("서버 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.", {
            description: `HTTP ${response.status}`,
          });
        }
        return response;
      },
    ],
  },
});

// 401 시 한 번만 토큰 자동 갱신을 시도한다(동시다발 401은 single-flight로 공유).
let refreshInFlight: Promise<boolean> | null = null;

function refreshSession(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = baseClient
      .post("auth/refresh", { throwHttpErrors: false, retry: 0 })
      .then((res) => res.ok)
      .catch(() => false)
      .finally(() => {
        refreshInFlight = null;
      });
  }
  return refreshInFlight;
}

interface SuccessEnvelope<T> {
  data: T;
}
interface ErrorEnvelope {
  error: { code: string; message: string; details?: Record<string, unknown> };
}

function isErrorEnvelope(v: unknown): v is ErrorEnvelope {
  return typeof v === "object" && v !== null && "error" in v;
}

function isSuccessEnvelope<T>(v: unknown): v is SuccessEnvelope<T> {
  return typeof v === "object" && v !== null && "data" in v;
}

async function request<T>(method: string, path: string, init?: Options): Promise<T> {
  try {
    const res = await baseClient(path, { ...init, method });
    const text = await res.text();
    if (!text) return undefined as T;

    const json: unknown = JSON.parse(text);
    if (isErrorEnvelope(json)) {
      throw new ApiError(json.error.code, json.error.message, res.status, json.error.details);
    }
    if (isSuccessEnvelope<T>(json)) {
      return json.data;
    }
    return json as T;
  } catch (err) {
    if (err instanceof ApiError) throw err;
    if (err instanceof HTTPError) {
      let envelope: unknown = null;
      try {
        envelope = await err.response.clone().json();
      } catch {
        envelope = null;
      }
      if (isErrorEnvelope(envelope)) {
        throw new ApiError(
          envelope.error.code,
          envelope.error.message,
          err.response.status,
          envelope.error.details,
        );
      }
      throw new ApiError("http_error", `HTTP ${err.response.status}`, err.response.status);
    }
    throw err;
  }
}

export const apiClient = {
  get: <T>(path: string, init?: Options) => request<T>("GET", path, init),
  post: <T>(path: string, init?: Options) => request<T>("POST", path, init),
  put: <T>(path: string, init?: Options) => request<T>("PUT", path, init),
  patch: <T>(path: string, init?: Options) => request<T>("PATCH", path, init),
  delete: <T>(path: string, init?: Options) => request<T>("DELETE", path, init),
};
