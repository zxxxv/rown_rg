import { useCallback } from "react";
import { toast } from "sonner";

export interface DownloadRequest {
  url: string;
  filename: string;
  label?: string;
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
      description: "현재는 시연용 더미 파일입니다. Phase 4에서 실 백엔드 변환으로 교체됩니다.",
    });
  }, []);
}
