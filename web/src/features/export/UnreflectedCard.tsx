import { AlertTriangle, ArrowRight } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { DRIFT_REASON_LABEL, useDrift } from "@/api/drift";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/** 미반영 절 카드 - 설계를 고쳤는데 본문이 아직 그걸 안 담고 있는 절을 알린다.
 *
 * 여기서 재작성을 자동으로 걸지 않는다: 절 하나가 실측 $0.67, 전체가 $15.5다.
 * 무엇이 왜 미반영인지만 보여주고 어디로 갈지 안내한다 - 실행은 사람이 고른다.
 */
export function UnreflectedCard({ projectId }: { projectId: string }) {
  const navigate = useNavigate();
  const { data } = useDrift(projectId);
  const sections = data?.sections ?? [];
  if (sections.length === 0) return null;

  return (
    <section className="flex flex-col gap-3 rounded-lg border border-fg-warning/30 bg-bg-warning p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-1.5 text-sm font-semibold text-fg">
          <AlertTriangle className="h-4 w-4 shrink-0 text-fg-warning" aria-hidden />
          미반영 절 {sections.length}개
        </h2>
        <Button
          variant="outline"
          size="sm"
          onClick={() => navigate(`/projects/${projectId}/preview`)}
        >
          본문에서 확인
          <ArrowRight className="ml-1 h-4 w-4" />
        </Button>
      </div>
      <p className="text-xs text-fg-secondary">
        설계를 고친 뒤 본문이 아직 그 내용을 담지 않았습니다. 본문이 틀린 것은 아니며, 다시 쓸지는
        고르시면 됩니다.
      </p>
      <ul className="flex flex-col gap-1.5">
        {sections.map((s) => (
          <li key={s.section_id} className="flex flex-wrap items-center gap-1.5 text-xs">
            <button
              type="button"
              className="font-medium text-fg underline-offset-2 hover:underline"
              onClick={() => navigate(`/projects/${projectId}/preview?section=${s.section_id}`)}
            >
              {s.label}
            </button>
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
    </section>
  );
}
