import { AlertTriangle, CheckCircle2, Circle, Quote } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import {
  type QaSelectCandidate,
  type QaSelectPayload,
  type QaSelectPlanEntry,
  useDecideQaSelect,
} from "@/api/checkpoints";
import { ApiError } from "@/api/client";
import { ReviewCheckpoint } from "@/components/data-display/ReviewCheckpoint";
import { Badge } from "@/components/ui/badge";
import { MarkdownContent } from "@/features/preview/MarkdownContent";
import { cn } from "@/lib/utils";

// 정적검사 체크 이름 → 사람용 라벨 (백엔드 services/qa/gate.py 어휘)
const WARNING_LABEL: Record<string, string> = {
  bounds: "분량·금칙어",
  numeric_grounded: "수치 근거",
  citation_resolves: "인용 해석",
  citation_markers: "인용 표기",
  renderable: "렌더 가능",
};

export interface QaSelectGateProps {
  projectId: string;
  payload: QaSelectPayload;
  /** 제출 성공 후 호출 — 게이트 배너를 걷고 진행 화면으로 복귀한다. */
  onResumed: () => void;
}

interface SectionRow {
  sectionId: string;
  label: string;
  plan: QaSelectPlanEntry | null;
  candidates: QaSelectCandidate[];
  allExcluded: boolean;
}

/** QA_SELECT 게이트 — 섹션별 후보 초안 중 사람이 하나씩 골라 제출한다. */
export function QaSelectGate({ projectId, payload, onResumed }: QaSelectGateProps) {
  const rows = useMemo<SectionRow[]>(() => {
    const planById = new Map(payload.section_plan.map((p) => [p.section_id, p]));
    // 표시 순서 = section_plan(목차) 순. plan에 없는 섹션은 뒤에 붙인다(방어).
    const ordered = [...payload.sections].sort((a, b) => {
      const ia = payload.section_plan.findIndex((p) => p.section_id === a.section_id);
      const ib = payload.section_plan.findIndex((p) => p.section_id === b.section_id);
      return (
        (ia === -1 ? Number.MAX_SAFE_INTEGER : ia) - (ib === -1 ? Number.MAX_SAFE_INTEGER : ib)
      );
    });
    return ordered.map((sec) => {
      const plan = planById.get(sec.section_id) ?? null;
      return {
        sectionId: sec.section_id,
        label: plan
          ? `${plan.chapter_number}.${plan.section_number} ${plan.title}`
          : sec.section_id,
        plan,
        candidates: sec.candidates,
        allExcluded: sec.all_excluded || sec.candidates.length === 0,
      };
    });
  }, [payload]);

  const selectable = useMemo(() => rows.filter((r) => !r.allExcluded), [rows]);
  const excludedCount = rows.length - selectable.length;

  const [selections, setSelections] = useState<Record<string, string>>({});
  const selectedCount = selectable.filter((r) => selections[r.sectionId]).length;
  // 선택 가능한 섹션이 0개(전부 all_excluded)여도 제출은 가능해야 한다 —
  // 빈 선택으로 재개하면 조립 단계 structure 검사가 누락을 표시한다.
  const complete = selectable.length === 0 || selectedCount === selectable.length;

  const decide = useDecideQaSelect();
  const onSubmit = () => {
    if (!complete || decide.isPending) return;
    // all_excluded 섹션은 제출에서 빠진다 — 조립 단계 structure 검사가 누락을 표시한다.
    const picked = Object.fromEntries(
      selectable.map((r) => [r.sectionId, selections[r.sectionId]]),
    );
    decide.mutate(
      { projectId, selections: picked },
      {
        onSuccess: () => {
          toast.success("후보 선택이 제출됐습니다.", {
            description: "선택한 초안으로 보고서 조립을 재개합니다.",
          });
          onResumed();
        },
        onError: (err) => {
          const msg = err instanceof ApiError ? err.message : "제출에 실패했습니다.";
          toast.error("선택 제출 실패", { description: msg });
        },
      },
    );
  };

  return (
    <ReviewCheckpoint
      number={3}
      title="본문 후보 선택 — QA 검토"
      description={
        payload.message ||
        "섹션별로 생성된 후보 초안 중 채택할 하나를 고르세요. 정적검사(HARD)를 통과한 후보만 표시됩니다."
      }
      decisions={[
        {
          label: decide.isPending
            ? "제출 중…"
            : selectable.length === 0
              ? "선택 없이 진행"
              : `선택 제출 (${selectedCount}/${selectable.length})`,
          intent: "primary",
          onClick: onSubmit,
          disabled: !complete || decide.isPending,
        },
      ]}
    >
      <div className="flex flex-col gap-6">
        {excludedCount > 0 ? (
          <div
            role="alert"
            className="flex items-start gap-2 rounded border border-fg-warning/40 bg-bg-warning p-3 text-sm"
          >
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-fg-warning" aria-hidden />
            <p className="text-fg-secondary">
              통과 후보가 없는 섹션 {excludedCount}개는 이번 제출에서 제외됩니다. 조립 후 편집
              화면에서 직접 작성하거나 AI 재작성으로 채울 수 있습니다.
            </p>
          </div>
        ) : null}

        {rows.map((row) => (
          <SectionBlock
            key={row.sectionId}
            row={row}
            selected={selections[row.sectionId]}
            onSelect={(candidateId) =>
              setSelections((prev) => ({ ...prev, [row.sectionId]: candidateId }))
            }
          />
        ))}
      </div>
    </ReviewCheckpoint>
  );
}

function SectionBlock({
  row,
  selected,
  onSelect,
}: {
  row: SectionRow;
  selected: string | undefined;
  onSelect: (candidateId: string) => void;
}) {
  return (
    <section aria-label={`섹션 ${row.label}`} className="flex flex-col gap-2">
      <header className="flex flex-wrap items-baseline gap-2">
        <h3 className="text-base font-semibold text-fg">{row.label}</h3>
        {row.plan?.analysts.length ? (
          <span className="text-xs text-fg-tertiary">
            담당 에이전트: {row.plan.analysts.join(", ")}
          </span>
        ) : null}
      </header>
      {row.plan?.direction ? (
        <p className="text-xs text-fg-secondary">작성 방향 — {row.plan.direction}</p>
      ) : null}

      {row.allExcluded ? (
        <div className="rounded border border-dashed border-border bg-bg-secondary p-4 text-sm text-fg-tertiary">
          정적검사를 통과한 후보가 없습니다 — 이 섹션은 선택 없이 넘어갑니다.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
          {row.candidates.map((cand, i) => (
            <CandidateCard
              key={cand.candidate_id}
              candidate={cand}
              label={`후보 ${String.fromCharCode(65 + i)}`}
              selected={selected === cand.candidate_id}
              onSelect={() => onSelect(cand.candidate_id)}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function CandidateCard({
  candidate,
  label,
  selected,
  onSelect,
}: {
  candidate: QaSelectCandidate;
  label: string;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onSelect}
      className={cn(
        "flex flex-col gap-2 rounded border p-3 text-left transition-colors",
        selected
          ? "border-accent bg-bg ring-2 ring-accent"
          : "border-border bg-bg hover:border-fg-tertiary",
      )}
    >
      <header className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-sm font-medium text-fg">
          {selected ? (
            <CheckCircle2 className="h-4 w-4 text-accent" aria-hidden />
          ) : (
            <Circle className="h-4 w-4 text-fg-tertiary" aria-hidden />
          )}
          {label}
        </span>
        <span className="flex items-center gap-2 text-xs text-fg-tertiary">
          <span className="flex items-center gap-1">
            <Quote className="h-3 w-3" aria-hidden />
            인용 {candidate.cited_chunk_ids.length}건
          </span>
          <span className="font-mono">{candidate.content.length.toLocaleString()}자</span>
        </span>
      </header>

      {candidate.warnings.length > 0 ? (
        <ul className="flex flex-col gap-1">
          {/* 정적검사는 check당 결과 1건 — check 이름이 곧 고유 키다 */}
          {candidate.warnings.map((w) => (
            <li key={w.check} className="flex items-start gap-1.5 text-xs text-fg-secondary">
              <Badge
                variant="outline"
                className="shrink-0 border-fg-warning/40 bg-bg-warning font-normal"
              >
                {WARNING_LABEL[w.check] ?? w.check}
              </Badge>
              {w.detail ? <span className="pt-0.5">{w.detail}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}

      {/* 후보 본문은 미리보기와 같은 마크다운 렌더로 — 원문 MD 노출은 가독성이 나빴다(2026-08-05) */}
      <div className="max-h-72 overflow-y-auto rounded border border-border bg-bg-secondary p-3 text-sm leading-relaxed text-fg">
        <MarkdownContent content={candidate.content} />
      </div>
    </button>
  );
}
