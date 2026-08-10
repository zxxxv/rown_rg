import { useCallback, useState } from "react";
import { toast } from "sonner";

export interface DownloadRequest {
  url: string;
  filename: string;
  label?: string;
  /** 토스트 부가 설명(생략 가능) */
  description?: string;
}

// ─── 파일 다운로드 ───
// <a download> 링크 클릭 방식은 서버가 파일을 만드는 동안(HWPX 재렌더는 수십 초)
// 화면에 아무 표시가 없고, 시작하지도 않은 다운로드를 "시작했습니다"라고 알렸다.
// 실패해도 조용했다(브라우저가 JSON 오류로 이동). fetch로 받아 진행·실패를 드러낸다.
// (2026-08-10 지적: "눌렀는데 바로 시작이 안되고 좀 이상해")

export function useDownload() {
  const [pending, setPending] = useState(false);

  const download = useCallback(
    async (req: DownloadRequest) => {
      if (pending) return;
      setPending(true);
      const label = req.label ?? req.filename;
      const toastId = toast.loading(`${label} 준비 중…`, {
        description: req.description ?? "파일을 만드는 중입니다. 잠시 기다려 주세요.",
      });
      try {
        // 쿠키 인증이라 credentials 필요. apiClient는 JSON 전용이라 여기선 fetch를 쓴다.
        const res = await fetch(req.url, { credentials: "include" });
        if (!res.ok) throw new Error(String(res.status));
        const blob = await res.blob();
        const href = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = href;
        a.download = req.filename;
        a.rel = "noopener";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        // 클릭 직후 해제하면 일부 브라우저가 저장을 놓친다 - 한 틱 뒤에 정리.
        setTimeout(() => URL.revokeObjectURL(href), 1000);
        toast.success(`${label} 다운로드`, { id: toastId, description: req.filename });
      } catch {
        toast.error(`${label} 다운로드 실패`, {
          id: toastId,
          description: "잠시 후 다시 시도해 주세요.",
        });
      } finally {
        setPending(false);
      }
    },
    [pending],
  );

  return { download, pending };
}
