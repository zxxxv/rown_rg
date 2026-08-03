import { Loader2 } from "lucide-react";

/** 페이지 단위 로딩 표시 — 빈 스켈레톤 대신 "불러오는 중"임을 명시해
 * 사용자가 오류로 오인하지 않게 한다(스피너 + 문구). */
export function PageLoading({ label = "불러오는 중…" }: { label?: string }) {
  return (
    <div
      role="status"
      className="flex min-h-[320px] flex-col items-center justify-center gap-3 rounded border border-border bg-bg"
    >
      <Loader2 className="h-7 w-7 animate-spin text-accent" aria-hidden />
      <p className="text-sm text-fg-secondary">{label}</p>
    </div>
  );
}
