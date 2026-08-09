import { CloudUpload, FileText } from "lucide-react";
import { useCallback } from "react";
import { useDropzone } from "react-dropzone";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface UploadingFile {
  id: string;
  name: string;
  progress: number;
}

export interface UploadDropzoneProps {
  onFiles: (files: File[]) => void;
  uploading: UploadingFile[];
  disabled?: boolean;
}

// 백엔드 색인 파서가 실제 지원하는 형식만 - PDF·HWPX·DOCX(문서), MD·TXT(플레인 텍스트).
const ACCEPT = {
  "application/pdf": [".pdf"],
  "application/x-hwpx": [".hwpx"],
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
  "text/markdown": [".md", ".markdown"],
  "text/plain": [".txt"],
};

export function UploadDropzone({ onFiles, uploading, disabled }: UploadDropzoneProps) {
  const onDrop = useCallback(
    (accepted: File[]) => {
      if (accepted.length > 0) onFiles(accepted);
    },
    [onFiles],
  );

  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    onDrop,
    accept: ACCEPT,
    disabled,
    noClick: true,
    noKeyboard: true,
  });

  return (
    <div className="flex flex-col gap-2">
      <section
        {...getRootProps()}
        aria-label="자료 업로드 드롭존"
        className={cn(
          "flex flex-col items-center gap-2 rounded-lg border border-dashed p-6 text-center transition-colors",
          isDragActive ? "border-accent bg-bg-info" : "border-border bg-bg-secondary",
          disabled && "opacity-60",
        )}
      >
        <input {...getInputProps()} />
        <CloudUpload className="h-8 w-8 text-fg-tertiary" aria-hidden />
        <p className="text-sm font-medium text-fg">
          {isDragActive ? "파일을 놓아주세요" : "자료를 끌어다 놓거나 버튼을 눌러 업로드"}
        </p>
        <p className="font-mono text-xs text-fg-tertiary">PDF · HWPX · DOCX · MD · TXT</p>
        <Button type="button" variant="outline" size="sm" onClick={open} disabled={disabled}>
          파일 선택
        </Button>
      </section>

      {uploading.length > 0 ? (
        <ul className="flex flex-col gap-1.5">
          {uploading.map((f) => (
            <li
              key={f.id}
              className="flex items-center gap-3 rounded border border-border bg-bg p-2.5"
            >
              <FileText className="h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
              <span className="flex-1 truncate text-sm text-fg-secondary">{f.name}</span>
              <span className="font-mono text-xs text-fg-tertiary">{f.progress}%</span>
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
      ) : null}
    </div>
  );
}
