import { AlertTriangle, FileUp, Loader2, RefreshCw } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { useProjectSources, useUploadProjectSource } from "@/api/sources";
import { Button } from "@/components/ui/button";

/**
 * 자료 부족 절의 회복 경로 - 경고에서 바로 자료를 올리고, 색인이 끝나면 그 절만 다시 쓴다.
 *
 * 경고만 있고 행동이 없으면 사용자는 자료 화면을 찾아 헤매게 된다(실사례: 숏폼 4.3
 * 특허·논문 동향, 관련 근거 사실상 0건). 업로드는 기존 소스 업로드 경로(백그라운드
 * 색인)를 그대로 쓰고, 색인 완료는 자료 목록 폴링(색인 중이면 4초)이 알려준다.
 * 다시 쓰기는 절 재작성(검색부터 다시)이라 새로 색인된 자료가 자동 반영된다.
 */
export function EvidenceBoost({
  projectId,
  count,
  editable,
  onRewrite,
  rewritePending,
  message,
}: {
  projectId: string;
  count: number | null;
  /** 생성이 끝난 절에서만 행동을 연다(작성 중 재작성은 작성 루프와 자원을 다툰다) */
  editable: boolean;
  onRewrite: () => void;
  rewritePending: boolean;
  /** 기본(자료 부족) 대신 쓸 경고 문구 - 목차 이행 경고 등 다른 원인에서 재사용 */
  message?: string;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const upload = useUploadProjectSource(projectId);
  // 이 배너에서 올린 자료만 추적한다 - 다른 화면의 업로드와 상태가 섞이지 않게.
  const [trackedIds, setTrackedIds] = useState<string[]>([]);
  const sourcesQuery = useProjectSources(projectId);
  const tracked = useMemo(
    () => (sourcesQuery.data?.items ?? []).filter((s) => trackedIds.includes(s.id)),
    [sourcesQuery.data, trackedIds],
  );
  const indexing = upload.isPending || tracked.some((s) => s.indexing);
  const failed = tracked.filter((s) => !s.indexing && s.index_error);
  const ready = tracked.filter((s) => !s.indexing && !s.index_error);

  const onFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    for (const file of Array.from(files)) {
      try {
        const row = await upload.mutateAsync(file);
        setTrackedIds((prev) => [...prev, row.id]);
      } catch (err) {
        const msg = err instanceof ApiError ? err.message : "업로드에 실패했습니다.";
        toast.error(`${file.name} 업로드 실패`, { description: msg });
      }
    }
    if (fileRef.current) fileRef.current.value = "";
  };

  return (
    <div className="flex flex-col gap-2 border-b border-fg-warning/30 bg-bg-warning px-6 py-2.5">
      <p className="flex items-center gap-1.5 text-xs text-fg">
        <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-fg-warning" aria-hidden />
        {message ??
          `자료 부족 절 - 확보된 근거${count !== null ? ` ${count}건` : ""}에 맞춰 분량을 줄여 작성했습니다. 자료를 추가하고 이 절만 다시 쓰면 채워집니다.`}
      </p>
      {editable ? (
        <div className="flex flex-wrap items-center gap-2 pl-5">
          <input
            ref={fileRef}
            type="file"
            multiple
            hidden
            accept=".pdf,.hwpx,.docx,.md,.txt"
            onChange={(e) => void onFiles(e.target.files)}
          />
          <Button
            variant="outline"
            size="sm"
            onClick={() => fileRef.current?.click()}
            disabled={upload.isPending || rewritePending}
          >
            <FileUp className="mr-1 h-3.5 w-3.5" aria-hidden />
            {upload.isPending ? "올리는 중…" : "자료 추가"}
          </Button>
          <Button size="sm" onClick={onRewrite} disabled={rewritePending || indexing}>
            {rewritePending ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <RefreshCw className="mr-1 h-3.5 w-3.5" aria-hidden />
            )}
            {rewritePending ? "다시 쓰는 중…" : "이 절 다시 쓰기"}
          </Button>
          {indexing && !upload.isPending ? (
            <span className="flex items-center gap-1 text-xs text-fg-secondary">
              <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
              색인 중 - 끝나면 다시 쓰기가 열립니다
            </span>
          ) : ready.length > 0 ? (
            <span className="text-xs text-fg-secondary">
              자료 {ready.length}건 색인 완료 - 다시 쓰면 반영됩니다
            </span>
          ) : null}
          {failed.map((s) => (
            <span key={s.id} className="text-xs text-fg-danger">
              {s.title}: {s.index_error}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}
