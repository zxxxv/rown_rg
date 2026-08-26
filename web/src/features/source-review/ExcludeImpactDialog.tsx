import { AlertTriangle, Loader2, Lock } from "lucide-react";
import { estimateLabel, useCostBasis } from "@/api/cost";
import { useSourceImpact } from "@/api/sources";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/** 자료를 빼기 전에 무엇이 걸려 있는지 보여준다.
 *
 * 제외는 조용한 파괴다 - 누르는 순간 인용이 다시 매겨지고 그 자료를 근거로 쓴 절은
 * 근거를 잃는다. 되돌리려면 다시 채택하고 그 절들을 다시 써야 하는데, 절당 실측
 * $0.4~$1.3짜리 되돌리기다. 그래서 값을 먼저 말한다.
 *
 * 인용이 하나도 없으면 이 창을 띄우지 않는다(부모가 판단) - 아무것도 안 걸린 제외까지
 * 확인을 받으면 창이 소음이 되고, 소음이 된 확인창은 읽지 않고 눌린다.
 */
export function ExcludeImpactDialog({
  projectId,
  source,
  onCancel,
  onConfirm,
  pending,
}: {
  projectId: string;
  source: { id: string; title: string } | null;
  onCancel: () => void;
  onConfirm: () => void;
  pending: boolean;
}) {
  const impact = useSourceImpact(projectId, source?.id ?? null);
  const costBasis = useCostBasis(projectId, Boolean(source));
  const data = impact.data;
  const sole = data?.sections.filter((s) => s.sole) ?? [];

  return (
    <Dialog open={source !== null} onOpenChange={(open) => (!open ? onCancel() : undefined)}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>이 자료를 빼면</DialogTitle>
          <DialogDescription>
            {source?.title} - 본문은 그대로 남고 근거만 빠집니다. 그 절은 개요에서 미반영으로
            표시되며, 다시 쓸지는 거기서 고르시면 됩니다.
          </DialogDescription>
        </DialogHeader>

        {impact.isLoading ? (
          <p className="flex items-center gap-2 text-sm text-fg-secondary">
            <Loader2 className="h-4 w-4 animate-spin" />
            영향을 확인하는 중…
          </p>
        ) : (
          <div className="flex flex-col gap-3 text-sm">
            <p className="text-fg">
              절 {data?.n_sections ?? 0}개 · 인용 {data?.n_citations ?? 0}건이 이 자료를 근거로 쓰고
              있습니다.
            </p>

            {sole.length > 0 ? (
              // 가장 아픈 경우 - 다른 근거가 하나도 없어 제외 후 무근거 서술만 남는다.
              <p className="flex items-start gap-1.5 rounded border border-fg-danger/30 bg-bg-danger px-3 py-2 text-xs text-fg">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-fg-danger" aria-hidden />
                <span>
                  이 중 {sole.length}개 절은 <b>이 자료가 유일한 근거</b>입니다. 빼면 근거 없는
                  서술만 남습니다.
                </span>
              </p>
            ) : null}

            {data && data.sections.length > 0 ? (
              <ul className="flex max-h-52 flex-col gap-1 overflow-y-auto text-xs">
                {data.sections.map((s) => (
                  <li key={s.section_id} className="flex flex-wrap items-center gap-1.5">
                    <span className="font-medium text-fg">{s.label}</span>
                    <span className="text-fg-tertiary">인용 {s.n_citations}건</span>
                    {s.sole ? (
                      <Badge variant="outline" className="border-fg-danger/40 text-fg-danger">
                        유일한 근거
                      </Badge>
                    ) : null}
                    {s.locked ? (
                      // 잠근 절은 다시 쓸 수도 없다 - 먼저 풀어야 복구 경로가 열린다.
                      <Badge variant="outline" className="gap-1 text-fg-tertiary">
                        <Lock className="h-3 w-3" aria-hidden />
                        잠김
                      </Badge>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : null}

            {data && data.n_sections > 0 && estimateLabel(costBasis.data, data.n_sections) ? (
              <p className="text-xs text-fg-secondary">
                다시 쓴다면 {estimateLabel(costBasis.data, data.n_sections)}
              </p>
            ) : null}
          </div>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={onCancel} disabled={pending}>
            취소
          </Button>
          <Button variant="destructive" onClick={onConfirm} disabled={pending}>
            {pending ? "빼는 중…" : "그래도 빼기"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
