import { useState } from "react";
import { usePromptPreview } from "@/api/prompts";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

// ─── 최종 프롬프트 조합 미리보기 ───
// 조립 규칙(기본 규칙 → 페르소나 → 작성 규칙, 분량 문구 생성)이 작성 경로 안에만
// 있어서 "만든 게 반영됐는지" 볼 방법이 없었다. 서버가 작성 때와 같은 함수로 조립해
// 돌려주므로, 여기 보이는 것이 곧 모델이 받는 것이다.

export interface PromptPreviewDialogProps {
  analysts: string[];
  rules: string[];
  title: string;
  direction: string;
  keyPoints: string[];
  onClose: () => void;
}

export function PromptPreviewDialog({
  analysts,
  rules,
  title,
  direction,
  keyPoints,
  onClose,
}: PromptPreviewDialogProps) {
  const [tab, setTab] = useState<"blocks" | "system" | "guidance">("blocks");
  const query = usePromptPreview(
    { analysts, rules, title, direction, key_points: keyPoints },
    true,
  );
  const data = query.data;
  const total = data?.blocks.reduce((n, b) => n + b.chars, 0) ?? 0;

  return (
    <Dialog open onOpenChange={(o) => (!o ? onClose() : undefined)}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>이 절을 쓸 때 모델이 받는 프롬프트</DialogTitle>
          <DialogDescription>
            작성 단계와 같은 함수로 조립한 결과입니다. 목표 분량 문구는 배정된 에이전트에서 자동
            생성됩니다.
          </DialogDescription>
        </DialogHeader>

        {query.isLoading ? (
          <p className="text-sm text-fg-tertiary">조립 중…</p>
        ) : query.error || !data ? (
          <p className="text-sm text-fg-danger">미리보기를 불러오지 못했습니다.</p>
        ) : (
          <div className="flex flex-col gap-3">
            {data.unknown_analysts.length > 0 ? (
              <p className="rounded border border-fg-warning/30 bg-bg-warning px-3 py-2 text-xs text-fg">
                카탈로그에 없는 에이전트가 무시됩니다: {data.unknown_analysts.join(", ")}
              </p>
            ) : null}
            <div className="flex flex-wrap gap-3 text-xs text-fg-secondary">
              <span>
                목표 분량{" "}
                <span className="font-medium text-fg">
                  {data.min_chars && data.max_chars
                    ? `${data.min_chars.toLocaleString()}~${data.max_chars.toLocaleString()}자`
                    : "없음(에이전트 미배정)"}
                </span>
              </span>
              <span>
                분할 <span className="font-medium text-fg">{data.n_parts}파트</span>
              </span>
              <span>
                시스템 프롬프트{" "}
                <span className="font-medium text-fg">{total.toLocaleString()}자</span>
              </span>
            </div>

            <div className="flex gap-1.5">
              {(
                [
                  ["blocks", "구성"],
                  ["system", "시스템 프롬프트"],
                  ["guidance", "절 지시"],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setTab(key)}
                  className={
                    tab === key
                      ? "rounded border border-accent bg-bg-info px-2.5 py-1 text-xs text-fg"
                      : "rounded border border-border bg-bg px-2.5 py-1 text-xs text-fg-secondary hover:border-fg-tertiary"
                  }
                >
                  {label}
                </button>
              ))}
            </div>

            {tab === "blocks" ? (
              <div className="flex flex-col gap-1">
                {data.blocks.map((b) => (
                  <div
                    key={b.label}
                    className="flex items-center gap-2 rounded border border-border bg-bg px-3 py-1.5"
                  >
                    <span className="flex-1 truncate text-sm text-fg">{b.label}</span>
                    <span className="font-mono text-xs text-fg-tertiary">
                      {b.chars.toLocaleString()}자
                    </span>
                    <span
                      className="h-1.5 rounded-full bg-accent"
                      style={{ width: `${Math.max(4, (b.chars / Math.max(1, total)) * 160)}px` }}
                      aria-hidden
                    />
                  </div>
                ))}
              </div>
            ) : (
              <pre className="max-h-[46vh] overflow-auto whitespace-pre-wrap rounded border border-border bg-bg-secondary p-3 text-xs text-fg-secondary">
                {tab === "system" ? data.system : data.guidance || "(지시 없음)"}
              </pre>
            )}
          </div>
        )}
        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            닫기
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
