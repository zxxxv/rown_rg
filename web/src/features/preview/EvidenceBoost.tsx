import { AlertTriangle, FileUp, Loader2, RefreshCw } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { useProjectSources, useUploadProjectSource } from "@/api/sources";
import { Button } from "@/components/ui/button";

/**
 * 자료 보강 액션 줄 - 자료를 올리고, 색인이 끝나면 그 절만 다시 쓴다.
 *
 * 완성된 보고서가 맘에 안 들 때 전체 재생성 대신 국소 회복을 쓰게 하는 장치
 * (2026-08-14 사용자 결정: 경고 있는 절만이 아니라 모든 완성 절의 일반 경로).
 * 업로드는 기존 소스 업로드 경로(백그라운드 색인)를 그대로 쓰고, 색인 완료는
 * 자료 목록 폴링(색인 중이면 4초)이 알려준다. 다시 쓰기는 절 재작성(검색부터
 * 다시)이라 새로 색인된 자료가 자동으로 근거 풀에 들어간다.
 */
export function EvidenceBoostActions({
  projectId,
  onRewrite,
  rewritePending,
}: {
  projectId: string;
  onRewrite: () => void;
  rewritePending: boolean;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const upload = useUploadProjectSource(projectId);
  // 이 액션 줄에서 올린 자료만 추적한다 - 다른 화면의 업로드와 상태가 섞이지 않게.
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
        const row = await upload.mutateAsync({ file });
        setTrackedIds((prev) => [...prev, row.id]);
      } catch (err) {
        const msg = err instanceof ApiError ? err.message : "업로드에 실패했습니다.";
        toast.error(`${file.name} 업로드 실패`, { description: msg });
      }
    }
    if (fileRef.current) fileRef.current.value = "";
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
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
  );
}

/** 자료 부족·목차 이행 경고 절의 강조 배너 - 원인 문구와 함께 회복 액션을 펼쳐 보여준다. */
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
  return (
    <div className="flex flex-col gap-2 border-b border-fg-warning/30 bg-bg-warning px-6 py-2.5">
      <p className="flex items-center gap-1.5 text-xs text-fg">
        <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-fg-warning" aria-hidden />
        {message ??
          `자료 부족 절 - 확보된 근거${count !== null ? ` ${count}건` : ""}에 맞춰 분량을 줄여 작성했습니다. 자료를 추가하고 이 절만 다시 쓰면 채워집니다.`}
      </p>
      {editable ? (
        <div className="pl-5">
          <EvidenceBoostActions
            projectId={projectId}
            onRewrite={onRewrite}
            rewritePending={rewritePending}
          />
        </div>
      ) : null}
    </div>
  );
}
