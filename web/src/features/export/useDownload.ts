import { useCallback } from "react";
import { toast } from "sonner";

export interface DownloadRequest {
  url: string;
  filename: string;
  label?: string;
  /** 토스트 부가 설명(생략 가능) */
  description?: string;
}

export function useDownload() {
  return useCallback((req: DownloadRequest) => {
    const a = document.createElement("a");
    a.href = req.url;
    a.download = req.filename;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    toast.success(`${req.label ?? req.filename} 다운로드를 시작했습니다`, {
      ...(req.description ? { description: req.description } : {}),
    });
  }, []);
}
