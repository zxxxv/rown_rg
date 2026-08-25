import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, Loader2, Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import {
  DRIFT_REASON_LABEL,
  invalidateAfterRewrite,
  useDrift,
  useRewriteBatch,
  useRewriteBatchStatus,
} from "@/api/drift";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";

/** 미반영 절 카드 - 설계를 고쳤는데 본문이 아직 그걸 안 담고 있는 절을 알리고,
 *  골라서 바로 다시 쓰게 한다.
 *
 * 자동으로 걸지 않는다: 절 하나가 실측 $0.67, 전체가 $15.5다. 목차 한 줄 고쳤다고
 * 자동으로 돌면 사고다. 무엇이 왜 미반영인지 보여주고 대상은 사람이 고른다.
 */
export function UnreflectedCard({ projectId }: { projectId: string }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { data } = useDrift(projectId);
  const status = useRewriteBatchStatus(projectId);
  const rewrite = useRewriteBatch(projectId);
  const [picked, setPicked] = useState<Set<string>>(new Set());

  const sections = data?.sections ?? [];
  const running = status.data?.running ?? false;
  const failures = Object.entries(status.data?.failures ?? {});

  // 묶음이 끝나는 순간 목록을 다시 읽는다 - 재작성이 지문을 다시 찍으므로
  // 성공한 절은 여기서 사라진다(루프가 닫히는 자리).
  const done = status.data?.done ?? 0;
  useEffect(() => {
    if (!running && done > 0) {
      invalidateAfterRewrite(qc, projectId);
      setPicked(new Set());
    }
  }, [running, done, qc, projectId]);

  if (sections.length === 0 && !running) return null;

  const toggle = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const start = () => {
    rewrite.mutate(
      { sectionIds: [...picked] },
      {
        onSuccess: () => {
          toast.success(`${picked.size}개 절을 다시 쓰고 있습니다`, {
            description: "절당 수십 초 걸립니다 - 끝나면 목록에서 사라집니다.",
          });
          void status.refetch();
        },
        onError: (err: unknown) => {
          const msg = err instanceof ApiError ? err.message : "재작성을 시작하지 못했습니다.";
          toast.error("재작성 실패", { description: msg });
        },
      },
    );
  };

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-fg-warning/30 bg-bg-warning p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-1.5 text-sm font-semibold text-fg">
          <AlertTriangle className="h-4 w-4 shrink-0 text-fg-warning" aria-hidden />
          미반영 절 {sections.length}개
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            disabled={picked.size === 0 || running || rewrite.isPending}
            onClick={start}
          >
            {running || rewrite.isPending ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" />
            ) : (
              <Sparkles className="mr-1 h-4 w-4" />
            )}
            {picked.size > 0 ? `선택 ${picked.size}개 다시 쓰기` : "다시 쓸 절 선택"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => navigate(`/projects/${projectId}/preview`)}
          >
            본문에서 확인
            <ArrowRight className="ml-1 h-4 w-4" />
          </Button>
        </div>
      </div>

      {running ? (
        <p className="text-xs text-fg-secondary">
          다시 쓰는 중 {done}/{status.data?.total ?? 0}
          {status.data?.current ? ` · ${status.data.current}` : ""}
        </p>
      ) : (
        <p className="text-xs text-fg-secondary">
          설계를 고친 뒤 본문이 아직 그 내용을 담지 않았습니다. 본문이 틀린 것은 아니며, 다시 쓸지는
          고르시면 됩니다. 절당 수십 초가 걸립니다.
        </p>
      )}

      <ul className="flex flex-col gap-1.5">
        {sections.map((s) => (
          <li key={s.section_id} className="flex flex-wrap items-center gap-1.5 text-xs">
            <Checkbox
              id={`drift-${s.section_id}`}
              checked={picked.has(s.section_id)}
              onCheckedChange={() => toggle(s.section_id)}
              disabled={running}
              aria-label={`${s.label} 다시 쓰기 선택`}
            />
            <label htmlFor={`drift-${s.section_id}`} className="cursor-pointer font-medium text-fg">
              {s.label}
            </label>
            {s.reasons.map((r) => (
              <Badge key={r} variant="outline" className="font-normal">
                {DRIFT_REASON_LABEL[r] ?? r}
              </Badge>
            ))}
            {s.excluded_sources.length > 0 ? (
              <span className="text-fg-tertiary">
                뺀 자료: {s.excluded_sources.map((x) => x.title).join(", ")}
              </span>
            ) : null}
          </li>
        ))}
      </ul>

      {failures.length > 0 ? (
        <ul className="flex flex-col gap-1 border-t border-fg-warning/20 pt-2 text-xs text-fg-danger">
          {failures.map(([label, reason]) => (
            <li key={label}>
              {label} - {reason}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
