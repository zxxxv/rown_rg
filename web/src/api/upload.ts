import { ApiError, apiUrl, refreshSession } from "@/api/client";

// ─── 진행률 있는 파일 업로드 ─────────────────────────────────────────────
// apiClient(ky/fetch)는 업로드 진행 이벤트가 없어 로딩바가 가짜 50%에 멈춰 보였다.
// XHR의 upload.onprogress로 실제 전송 바이트를 읽는다. 서버는 두 업로드 모두
// 요청 안에서 전송+저장까지만 하고(색인은 백그라운드) 전송 진행률이 곧 요청 진행률이다.

export interface UploadOptions {
  /** 전송 진행률(0~100, 정수). 전송이 끝나도 응답 전이면 100에서 머문다. */
  onProgress?: (percent: number) => void;
  /** 기본 120초 - 수십 MB 파일도 넉넉히. (ky 기본 30초는 큰 파일이 잘렸다) */
  timeoutMs?: number;
}

interface ErrorEnvelope {
  error: { code: string; message: string; details?: Record<string, unknown> };
}

function isErrorEnvelope(v: unknown): v is ErrorEnvelope {
  return typeof v === "object" && v !== null && "error" in v;
}

/** FastAPI HTTPException({detail}) 폴백 - client.ts와 같은 규칙. */
function detailMessage(v: unknown): string | null {
  if (typeof v !== "object" || v === null || !("detail" in v)) return null;
  const detail = (v as { detail: unknown }).detail;
  return typeof detail === "string" && detail.trim() ? detail : null;
}

function parseBody<T>(xhr: XMLHttpRequest): T {
  const text = xhr.responseText;
  if (!text) return undefined as T;
  let json: unknown;
  try {
    json = JSON.parse(text);
  } catch {
    return undefined as T;
  }
  if (isErrorEnvelope(json)) {
    throw new ApiError(json.error.code, json.error.message, xhr.status, json.error.details);
  }
  // {data} 단일 키 봉투(MSW 목업)만 벗긴다 - client.ts와 같은 관용 규칙.
  if (
    typeof json === "object" &&
    json !== null &&
    !Array.isArray(json) &&
    "data" in json &&
    Object.keys(json).length === 1
  ) {
    return (json as { data: T }).data;
  }
  return json as T;
}

function send<T>(path: string, form: FormData, opts: UploadOptions): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", apiUrl(path));
    xhr.withCredentials = true; // 쿠키 인증 - apiClient의 credentials:"include"와 동일
    xhr.timeout = opts.timeoutMs ?? 120_000;

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) opts.onProgress?.(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onload = () => {
      try {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(parseBody<T>(xhr));
          return;
        }
        // 오류 본문에서 봉투/detail을 읽는다 - 실패해도 아래 폴백 메시지로.
        parseBody<T>(xhr); // 봉투면 여기서 ApiError를 던진다
        const detail = detailMessage(JSON.parse(xhr.responseText || "null"));
        reject(
          new ApiError(
            "http_error",
            detail ??
              (xhr.status >= 500
                ? "서버에 문제가 발생했습니다. 잠시 후 다시 시도해 주세요."
                : "요청을 처리할 수 없습니다. 입력을 확인해 주세요."),
            xhr.status,
          ),
        );
      } catch (err) {
        reject(err);
      }
    };
    xhr.onerror = () =>
      reject(new ApiError("network_error", "네트워크 연결을 확인해 주세요.", xhr.status));
    xhr.ontimeout = () =>
      reject(new ApiError("timeout", "업로드 시간이 초과됐습니다. 다시 시도해 주세요.", 0));
    xhr.send(form);
  });
}

/** FormData POST + 전송 진행률. 401이면 세션 1회 갱신 후 재시도(apiClient와 동일 정책). */
export async function uploadWithProgress<T>(
  path: string,
  form: FormData,
  opts: UploadOptions = {},
): Promise<T> {
  try {
    return await send<T>(path, form, opts);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401 && (await refreshSession())) {
      return send<T>(path, form, opts);
    }
    throw err;
  }
}
