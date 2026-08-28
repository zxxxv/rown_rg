import { useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, EyeOff, Loader2, Lock, Sparkles, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { estimateLabel, useCostBasis } from "@/api/cost";
import {
  DRIFT_REASON_LABEL,
  invalidateAfterRewrite,
  useCancelRewriteBatch,
  useDismissDrift,
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
  const cancel = useCancelRewriteBatch(projectId);
  const dismiss = useDismissDrift(projectId);
  const costBasis = useCostBasis(projectId);
  const [picked, setPicked] = useState<Set<string>>(new Set());

  const sections = data?.sections ?? [];
  // 미반영 절을 이어받는 절 - 미반영은 아니지만 전제가 바뀔 수 있어 후보로만 보인다.
  const related = data?.related ?? [];
  // 잠긴 절은 고를 수 없다 - 골라 봐야 서버가 덜어낸다. 목록에는 남긴다:
  // 잠갔다고 설계와 어긋난 사실이 사라지지는 않는다.
  const pickable = new Set([
    ...sections.filter((s) => !s.locked).map((s) => s.section_id),
    ...related.filter((r) => !r.locked).map((r) => r.section_id),
  ]);
  const chosen = [...picked].filter((id) => pickable.has(id));
  // '이대로 두기'는 미반영 표시를 지우는 동작이라 미반영 절에만 뜻이 있다 -
  // 이어받는 절은 표시할 어긋남 자체가 없으니 걸러 보낸다.
  const drifted = new Set(sections.map((s) => s.section_id));
  const chosenDrifted = chosen.filter((id) => drifted.has(id));
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

  const onDismiss = () => {
    dismiss.mutate(chosenDrifted, {
      onSuccess: (res) => {
        setPicked(new Set());
        if (res.dismissed.length > 0) {
          toast.success(`${res.dismissed.length}개 절을 이대로 두었습니다`, {
            description: "본문은 그대로입니다. 설계를 또 고치면 다시 표시됩니다.",
          });
        }
        if (res.skipped.length > 0) {
          toast.warning("본문이 없는 절은 넘길 수 없습니다", {
            description: `${res.skipped.join(", ")} - 먼저 작성해야 합니다.`,
          });
        }
      },
      onError: (err: unknown) => {
        const msg = err instanceof ApiError ? err.message : "표시를 지우지 못했습니다.";
        toast.error("이대로 두기 실패", { description: msg });
      },
    });
  };

  const start = () => {
    rewrite.mutate(
      { sectionIds: chosen },
      {
        onSuccess: () => {
          toast.success(`${chosen.length}개 절을 다시 쓰고 있습니다`, {
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
            disabled={chosen.length === 0 || running || rewrite.isPending}
            onClick={start}
          >
            {running || rewrite.isPending ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" />
            ) : (
              <Sparkles className="mr-1 h-4 w-4" />
            )}
            {chosen.length > 0 ? `선택 ${chosen.length}개 다시 쓰기` : "다시 쓸 절 선택"}
          </Button>
          {/* 다시 쓰지 않고 넘기는 길. 미반영은 "다시 써야 한다"가 아니라 "계약이
              바뀌었다"는 사실이라, 본문이 이미 그 내용을 담고 있으면 $0.67을 태울
              이유가 없다. 표시만 지우고 본문은 건드리지 않는다. */}
          <Button
            variant="outline"
            size="sm"
            disabled={chosenDrifted.length === 0 || running || dismiss.isPending}
            onClick={onDismiss}
          >
            <EyeOff className="mr-1 h-4 w-4" />
            이대로 두기
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

      {/* 예상 비용 - 고른 개수 x 이 보고서 절당 실측. 근거(자기 실측인지 남의 평균인지)
          까지 밝힌다: 추정의 출처를 숨기면 숫자를 믿을 수도, 의심할 수도 없다. */}
      {!running && chosen.length > 0 && estimateLabel(costBasis.data, chosen.length) ? (
        <p className="text-xs font-medium text-fg">
          {estimateLabel(costBasis.data, chosen.length)}
        </p>
      ) : null}

      {running ? (
        <p className="flex flex-wrap items-center gap-2 text-xs text-fg-secondary">
          <span>
            다시 쓰는 중 {done}/{status.data?.total ?? 0}
            {status.data?.current ? ` · ${status.data.current}` : ""}
          </span>
          {status.data?.cancelled ? (
            <span className="text-fg-tertiary">멈추는 중 - 지금 절만 마칩니다</span>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-xs"
              disabled={cancel.isPending}
              onClick={() => cancel.mutate()}
            >
              <XCircle className="mr-1 h-3.5 w-3.5" />
              멈추기
            </Button>
          )}
        </p>
      ) : (
        <p className="text-xs text-fg-secondary">
          설계를 고친 뒤 본문이 아직 그 내용을 담지 않았습니다. 본문이 틀린 것은 아니며, 다시 쓸지는
          고르시면 됩니다. 절당 수십 초가 걸립니다.
        </p>
      )}

      {/* 전체 선택 - 고를 수 있는 것 전부(잠긴 절 제외, 이어받는 절 포함). 몇 개가
          걸리는지 라벨에 밝힌다 - 아래 예상 비용 줄과 함께 놀람 없는 일괄 선택. */}
      {!running && pickable.size > 1 ? (
        <div className="flex items-center gap-1.5 text-xs">
          <Checkbox
            id="drift-select-all"
            checked={[...pickable].every((id) => picked.has(id))}
            onCheckedChange={() =>
              setPicked(
                [...pickable].every((id) => picked.has(id)) ? new Set() : new Set(pickable),
              )
            }
            aria-label="전체 선택"
          />
          <label htmlFor="drift-select-all" className="cursor-pointer text-fg-secondary">
            전체 선택 ({pickable.size}개{related.length > 0 ? " - 이어받는 절 포함" : ""})
          </label>
        </div>
      ) : null}

      <ul className="flex flex-col gap-1.5">
        {sections.map((s) => (
          <li key={s.section_id} className="flex flex-wrap items-center gap-1.5 text-xs">
            <Checkbox
              id={`drift-${s.section_id}`}
              checked={picked.has(s.section_id) && !s.locked}
              onCheckedChange={() => toggle(s.section_id)}
              disabled={running || s.locked}
              aria-label={`${s.label} 다시 쓰기 선택`}
            />
            <label htmlFor={`drift-${s.section_id}`} className="cursor-pointer font-medium text-fg">
              {s.label}
            </label>
            {s.locked ? (
              <Badge variant="outline" className="gap-1 font-normal text-fg-tertiary">
                <Lock className="h-3 w-3" aria-hidden />
                잠김
              </Badge>
            ) : null}
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

      {related.length > 0 ? (
        <div className="flex flex-col gap-1.5 border-t border-fg-warning/20 pt-2">
          <p className="text-xs text-fg-secondary">
            아래는 위 절을 <b className="text-fg">이어받는</b> 절입니다. 설계가 바뀐 것은 아니지만,
            위 절을 다시 쓰면 이어받는 내용의 전제가 바뀔 수 있습니다. 함께 다시 쓸지는 직접
            고르세요 - 자동으로 선택하지 않습니다.
          </p>
          <ul className="flex flex-col gap-1.5">
            {related.map((r) => (
              <li key={r.section_id} className="flex flex-wrap items-center gap-1.5 text-xs">
                <Checkbox
                  id={`drift-rel-${r.section_id}`}
                  checked={picked.has(r.section_id) && !r.locked}
                  onCheckedChange={() => toggle(r.section_id)}
                  disabled={running || r.locked}
                  aria-label={`${r.label} 함께 다시 쓰기 선택`}
                />
                <label
                  htmlFor={`drift-rel-${r.section_id}`}
                  className="cursor-pointer font-medium text-fg"
                >
                  {r.label}
                </label>
                {r.locked ? (
                  <Badge variant="outline" className="gap-1 font-normal text-fg-tertiary">
                    <Lock className="h-3 w-3" aria-hidden />
                    잠김
                  </Badge>
                ) : null}
                <span className="text-fg-tertiary">{r.via.join(", ")}을(를) 이어받음</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

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
