import { FileText } from "lucide-react";

// 업로드 진행 행 - 자료 업로드(프로젝트)와 라이브러리 업로드가 같은 모양을 쓴다.
export interface UploadingFile {
  id: string;
  name: string;
  /** 실제 전송 진행률(0~100). XHR upload.onprogress가 채운다. */
  progress: number;
}

export function UploadProgressList({ uploading }: { uploading: UploadingFile[] }) {
  if (uploading.length === 0) return null;
  return (
    <ul className="flex flex-col gap-1.5">
      {uploading.map((f) => (
        <li key={f.id} className="flex items-center gap-3 rounded border border-border bg-bg p-2.5">
          <FileText className="h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
          <span className="flex-1 truncate text-sm text-fg-secondary">{f.name}</span>
          {/* 전송이 끝나면 서버가 저장을 마칠 때까지 응답을 기다린다 - 숫자 대신 상태로 */}
          <span className="font-mono text-xs text-fg-tertiary">
            {f.progress >= 100 ? "저장 중…" : `${f.progress}%`}
          </span>
          <div
            className="h-1.5 w-24 overflow-hidden rounded-full bg-bg-tertiary"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={f.progress}
          >
            <div
              className="h-full bg-accent transition-[width]"
              style={{ width: `${f.progress}%` }}
            />
          </div>
        </li>
      ))}
    </ul>
  );
}
