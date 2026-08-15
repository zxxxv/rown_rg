import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Check,
  Loader2,
  Pencil,
  RefreshCw,
  Search,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import {
  AiPlanSchema,
  type BriefSection,
  type DesignBriefPayload,
  decideDesignBrief,
  getDesignBrief,
  parseDesignBriefPayload,
} from "@/api/checkpoints";
import { ApiError } from "@/api/client";
import { progressKeys, useProgressSnapshot } from "@/api/progress";
import { useProject } from "@/api/projects";
import type { Outline } from "@/api/types";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  type DraftChapter,
  draftId,
  OutlineEditor,
  toOutline,
} from "@/features/project-config/OutlineEditor";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";

/** 브리프 절 목록 → 목차 편집기 초안. 편집은 생성 화면과 같은 편집기를 재사용한다. */
function toDraftChapters(sections: BriefSection[]): DraftChapter[] {
  const byChapter = new Map<number, DraftChapter>();
  for (const s of sections) {
    let chapter = byChapter.get(s.chapter_number);
    if (!chapter) {
      chapter = { _id: draftId(), title: s.chapter_title, sections: [] };
      byChapter.set(s.chapter_number, chapter);
    }
    chapter.sections.push({
      _id: draftId(),
      title: s.title,
      direction: s.direction,
      key_points: s.key_points,
      analysts: s.analysts,
    });
  }
  return [...byChapter.entries()].sort((a, b) => a[0] - b[0]).map(([, c]) => c);
}

const MODE_LABEL: Record<string, string> = {
  economy: "절약",
  standard: "표준",
  premium: "고급",
};

/** 시작 전 규모 - "비싼 런"이 얼마짜리인지 먼저 보여준다(실측 단가 기반 범위 추정).
    남은 한도가 예상 비용에 못 미쳐도 차단하지 않는다 - 숫자 두 개를 나란히 보여주고
    판단은 사람이 한다(경고만, 2026-08-15 결정). */
function EstimateCard({ brief }: { brief: DesignBriefPayload }) {
  const est = brief.estimate;
  if (!est || est.n_sections === 0) return null;
  const remaining = est.remaining_limit_usd;
  // 확실히 부족(최소 추정도 초과) vs 빠듯(최대 추정이 초과) - 문구를 구분한다.
  const short = remaining != null && est.cost_usd_min > remaining;
  const tight = remaining != null && !short && est.cost_usd_max > remaining;
  return (
    <div className="flex flex-col gap-1.5 rounded-lg border border-border bg-bg-secondary px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
        <span className="text-sm font-medium text-fg">예상 규모</span>
        <span className="text-sm text-fg-secondary">{est.n_sections}개 절</span>
        <span className="text-sm text-fg-secondary">
          {est.total_min_chars.toLocaleString()}~{est.total_max_chars.toLocaleString()}자 (A4{" "}
          {est.pages_min}~{est.pages_max}쪽)
        </span>
        <span className="text-sm text-fg-secondary">
          예상 비용 ${est.cost_usd_min}~${est.cost_usd_max} (
          {MODE_LABEL[est.model_mode] ?? est.model_mode} 모드)
        </span>
        {remaining != null ? (
          <span className={cn("text-sm", short || tight ? "text-fg-warning" : "text-fg-secondary")}>
            이번 달 남은 한도 ${remaining}
          </span>
        ) : null}
        <span className="text-[11px] text-fg-tertiary">과거 실측 단가 기반 추정입니다</span>
      </div>
      {short || tight ? (
        <p className="flex items-center gap-1.5 text-xs text-fg-warning">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
          {short
            ? "예상 비용이 남은 한도를 넘습니다 - 진행하면 도중에 한도에 걸려 멈출 수 있습니다. 절약 모드로 낮추거나 관리자에게 한도 조정을 요청하세요."
            : "예상 비용 상단이 남은 한도에 빠듯합니다 - 도중에 한도에 걸릴 수 있습니다."}
        </p>
      ) : null}
    </div>
  );
}

/** 같은 질의를 쓰는 절이 있으면 그 사실을 먼저 알린다 - 겹치는 걸 지적하는 편이
    있는 걸 검토하라는 것보다 잘 걸린다. AI가 갈래 질의를 제안했으면 함께 보여준다. */
function DuplicateWarning({ brief }: { brief: DesignBriefPayload }) {
  if (brief.duplicate_queries.length === 0) return null;
  const splits = new Map(
    (brief.ai_plan?.query_splits ?? []).map((q) => [q.section, q.query] as const),
  );
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border-danger bg-bg-danger-subtle p-4">
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-fg-danger" />
        <div className="flex flex-col gap-1">
          <p className="text-sm font-medium text-fg-danger">
            {brief.warnings.duplicate_query_sections}개 절이 똑같은 검색 질의를 씁니다
          </p>
          <p className="text-xs text-fg-secondary">
            검색은 질의가 같으면 같은 결과를 돌려줍니다. 이대로 두면 아래 절들이 같은 자료를
            인용하고, 장이 달라도 서로 구별되지 않는 글이 나옵니다. 장 제목이나 절 제목을 서로
            다르게 하면 갈라집니다.
          </p>
        </div>
      </div>
      <ul className="flex flex-col gap-2">
        {brief.duplicate_queries.map((group) => (
          <li key={group.query} className="rounded-md bg-bg p-3 text-xs">
            <code className="break-all font-mono text-fg">{group.query || "(빈 질의)"}</code>
            <p className="mt-1 text-fg-secondary">
              {group.sections.map((s) => s.label).join(" · ")}
            </p>
            {group.sections.some((s) => splits.has(s.label)) ? (
              <div className="mt-2 flex flex-col gap-0.5 border-t border-border pt-2">
                <p className="text-[11px] text-fg-tertiary">AI 제안 - 목차를 고칠 때 참고:</p>
                {group.sections.map((s) => {
                  const suggested = splits.get(s.label);
                  return suggested ? (
                    <p key={s.label} className="text-[11px] text-fg-secondary">
                      {s.label} → <code className="font-mono">{suggested}</code>
                    </p>
                  ) : null;
                })}
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

// 그래프 색 - ChartBlock과 같은 CSS 변수 규약(미정의 시 폴백 hex).
const FLOW_EDGE = "var(--chart-ink, #a3a19c)";
const FLOW_CROSS = "var(--chart-accent, #c26d3f)";
const FLOW_GRID = "var(--chart-grid, #d6d5d2)";
const FLOW_INK = "var(--chart-ink, #52514e)";

/** 절 간 흐름 그래프 - 행=장, 열=절, 화살표=산출을 받아 쓰는 관계.
 *
 * 22건을 텍스트 목록으로 늘어놓으면 훑을 수 없다(2026-08-14 지적). 그림에서는
 * "장마다 같은 사슬 + 장을 건너는 점선 몇 개"라는 구조가 한눈에 잡힌다.
 * 무엇을 받는지(carries)는 선 툴팁과 각 절 카드의 '← 받는 입력' 줄이 담당한다.
 */
function FlowGraph({ brief }: { brief: DesignBriefPayload }) {
  const plan = brief.ai_plan;
  const flows = plan?.flows ?? [];
  if (!plan || (flows.length === 0 && plan.orphans.length === 0)) return null;

  const PAD = 14;
  const CELL_W = 88;
  const CELL_H = 58;
  const NODE_W = 48;
  const NODE_H = 26;
  const chapters = [...new Set(brief.sections.map((s) => s.chapter_number))].sort((a, b) => a - b);
  const rowOf = new Map(chapters.map((c, i) => [c, i] as const));
  const maxCol = Math.max(1, ...brief.sections.map((s) => s.section_number));
  const width = PAD * 2 + (maxCol - 1) * CELL_W + NODE_W;
  const height = PAD * 2 + (chapters.length - 1) * CELL_H + NODE_H;
  const orphanSet = new Set(plan.orphans);

  const pos = (label: string) => {
    const [c, s] = label.split(".").map(Number);
    const row = rowOf.get(c);
    if (row === undefined || !s) return null;
    const x = PAD + (s - 1) * CELL_W;
    const y = PAD + row * CELL_H;
    return { x, y, xc: x + NODE_W / 2, yc: y + NODE_H / 2 };
  };

  const jump = (label: string) =>
    document
      .getElementById(`brief-sec-${label}`)
      ?.scrollIntoView({ behavior: "smooth", block: "center" });

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <p className="text-xs font-medium text-fg">절 간 흐름 (AI 판단)</p>
        {/* 범례는 견본으로 - 설명 문단은 읽히지 않는다(2026-08-14 지적). 전달 내용은
            선 툴팁과 각 절의 "←" 줄이 담당하므로 여기서 말로 안내하지 않는다. */}
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-fg-tertiary">
          <span className="inline-flex items-center gap-1.5">
            <svg width="26" height="8" aria-hidden="true">
              <line x1="0" y1="4" x2="19" y2="4" stroke={FLOW_EDGE} strokeWidth="1.4" />
              <path d="M 19 0.5 L 26 4 L 19 7.5 z" fill={FLOW_EDGE} />
            </svg>
            산출을 받음
          </span>
          <span className="inline-flex items-center gap-1.5">
            <svg width="26" height="8" aria-hidden="true">
              <line
                x1="0"
                y1="4"
                x2="19"
                y2="4"
                stroke={FLOW_CROSS}
                strokeWidth="1.4"
                strokeDasharray="4 3"
              />
              <path d="M 19 0.5 L 26 4 L 19 7.5 z" fill={FLOW_CROSS} />
            </svg>
            다른 장으로 전달
          </span>
          <span className="inline-flex items-center gap-1.5">
            <svg width="18" height="12" aria-hidden="true">
              <rect
                x="1"
                y="1"
                width="16"
                height="10"
                rx="3"
                fill="var(--chart-node, #ffffff)"
                stroke={FLOW_CROSS}
                strokeDasharray="3 2"
              />
            </svg>
            연결 없음
          </span>
        </div>
      </div>
      <div className="overflow-x-auto">
        <svg width={width} height={height} role="img" aria-label="절 간 흐름 그래프">
          <defs>
            <marker
              id="flow-arrow"
              viewBox="0 0 8 8"
              refX="7"
              refY="4"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 8 4 L 0 8 z" fill={FLOW_EDGE} />
            </marker>
            <marker
              id="flow-arrow-cross"
              viewBox="0 0 8 8"
              refX="7"
              refY="4"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 8 4 L 0 8 z" fill={FLOW_CROSS} />
            </marker>
          </defs>
          {flows.map((f) => {
            const a = pos(f.from);
            const b = pos(f.to);
            if (!a || !b) return null;
            const cross = f.from.split(".")[0] !== f.to.split(".")[0];
            const sameRow = a.y === b.y;
            let d: string;
            if (sameRow && Math.abs(b.xc - a.xc) <= CELL_W + 1) {
              // 이웃 절 - 곧은 화살표
              d = `M ${a.x + NODE_W} ${a.yc} L ${b.x - 2} ${b.yc}`;
            } else if (sameRow) {
              // 같은 장에서 절을 건너뜀 - 행 위로 아치
              d = `M ${a.xc + NODE_W / 4} ${a.y} Q ${(a.xc + b.xc) / 2} ${a.y - 26} ${
                b.xc - NODE_W / 4
              } ${b.y - 2}`;
            } else {
              // 장을 건너는 관계 - 아래(위)로 완만한 곡선
              d = `M ${a.xc} ${a.y + NODE_H} C ${a.xc} ${a.y + NODE_H + 30} ${b.xc} ${
                b.y - 30
              } ${b.xc} ${b.y - 2}`;
            }
            const tip = `${f.from} → ${f.to}${f.carries ? ` : ${f.carries}` : ""}`;
            return (
              <g key={`${f.from}-${f.to}-${f.carries}`}>
                <path
                  d={d}
                  fill="none"
                  stroke={cross ? FLOW_CROSS : FLOW_EDGE}
                  strokeWidth={1.4}
                  strokeDasharray={cross ? "4 3" : undefined}
                  markerEnd={cross ? "url(#flow-arrow-cross)" : "url(#flow-arrow)"}
                />
                {/* 얇은 곡선은 툴팁을 잡기 어렵다 - 투명한 굵은 히트 영역을 겹친다 */}
                <path d={d} fill="none" stroke="transparent" strokeWidth={10}>
                  <title>{tip}</title>
                </path>
              </g>
            );
          })}
          {brief.sections.map((s) => {
            const label = `${s.chapter_number}.${s.section_number}`;
            const p = pos(label);
            if (!p) return null;
            const orphan = orphanSet.has(label);
            return (
              <a
                key={label}
                href={`#brief-sec-${label}`}
                aria-label={`${label} ${s.title} 절로 이동`}
                onClick={(e) => {
                  e.preventDefault();
                  jump(label);
                }}
                className="cursor-pointer"
              >
                <rect
                  x={p.x}
                  y={p.y}
                  width={NODE_W}
                  height={NODE_H}
                  rx={6}
                  fill="var(--chart-node, #ffffff)"
                  stroke={orphan ? FLOW_CROSS : FLOW_GRID}
                  strokeDasharray={orphan ? "3 2" : undefined}
                />
                <text
                  x={p.xc}
                  y={p.yc + 4}
                  textAnchor="middle"
                  fontSize={11}
                  fontFamily="monospace"
                  fill={FLOW_INK}
                >
                  {label}
                </text>
                <title>
                  {label} {s.title}
                </title>
              </a>
            );
          })}
        </svg>
      </div>
      {plan.orphans.length > 0 ? (
        <p className="text-[11px] text-fg-tertiary">
          앞 절 산출을 쓰지 않는 절(점선 상자): {plan.orphans.join(" · ")} - 독립적이어도 정상일 수
          있지만, 받아야 할 절이 빠진 것일 수도 있습니다.
        </p>
      ) : null}
    </div>
  );
}

interface PlanFields {
  goal: string;
  source_strategy: string;
  writing_plan: string;
}

const PLAN_FIELD_LABEL: Record<keyof PlanFields, string> = {
  goal: "목표",
  source_strategy: "자료",
  writing_plan: "구성",
};

function SectionRow({
  section,
  duplicated,
  plan,
  edited,
  onPlanChange,
  incoming,
}: {
  section: BriefSection;
  duplicated: boolean;
  plan?: PlanFields;
  /** 사람이 이 절의 계획을 고쳤는가 - 표시(수정됨 배지)용 */
  edited?: boolean;
  /** 계획 편집 콜백 - 승인 시 수정본이 AI 원안 대신 작성 계약으로 커밋된다 */
  onPlanChange?: (label: string, fields: PlanFields) => void;
  /** 이 절이 앞 절들에서 받는 입력(AI 판단 flows의 수신측) */
  incoming?: { from: string; carries: string }[];
}) {
  const [editingPlan, setEditingPlan] = useState(false);
  const label = `${section.chapter_number}.${section.section_number}`;
  return (
    <li
      id={`brief-sec-${section.chapter_number}.${section.section_number}`}
      className="flex flex-col gap-1.5 border-b border-border px-4 py-3 last:border-b-0"
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-fg">
          {section.chapter_number}.{section.section_number} {section.title}
        </span>
        {section.analysts.map((a) => (
          <Badge key={a} variant="secondary">
            {a}
          </Badge>
        ))}
        {section.volume ? (
          <span className="text-xs text-fg-tertiary">
            목표 {section.volume.min_chars.toLocaleString()}~
            {section.volume.max_chars.toLocaleString()}자
          </span>
        ) : null}
      </div>
      {section.direction ? <p className="text-xs text-fg-secondary">{section.direction}</p> : null}
      <div className="flex items-start gap-1.5">
        <Search className="mt-0.5 h-3 w-3 shrink-0 text-fg-tertiary" />
        <code
          className={
            duplicated
              ? "break-all font-mono text-xs text-fg-danger"
              : "break-all font-mono text-xs text-fg-secondary"
          }
        >
          {section.search_query || "(빈 질의)"}
        </code>
      </div>
      {incoming?.length ? (
        <div className="flex flex-col gap-0.5">
          {incoming.map((f) => (
            <p key={f.from} className="text-[11px] text-fg-tertiary">
              <span className="font-mono text-fg-secondary">← {f.from}</span>
              {f.carries ? ` ${f.carries}` : ""}
            </p>
          ))}
        </div>
      ) : null}
      {plan ? (
        // 승인하면 이 계획이 작성 프롬프트에 실린다(config._design_plan) - 장식이 아니라 계약.
        // 그래서 사람이 여기서 직접 고칠 수 있어야 한다(연필) - 고치면 수정본이 커밋된다.
        <div className="mt-1 flex flex-col gap-1 rounded-md bg-bg-secondary px-3 py-2">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-medium text-fg">실행 계획</span>
            {edited ? (
              <Badge variant="secondary" className="text-[10px]">
                수정됨
              </Badge>
            ) : null}
            {onPlanChange ? (
              <button
                type="button"
                className="ml-auto text-fg-tertiary hover:text-fg"
                aria-label={`${label} 실행 계획 ${editingPlan ? "닫기" : "고치기"}`}
                onClick={() => setEditingPlan((v) => !v)}
              >
                {editingPlan ? <Check className="h-3.5 w-3.5" /> : <Pencil className="h-3 w-3" />}
              </button>
            ) : null}
          </div>
          {editingPlan && onPlanChange ? (
            <div className="flex flex-col gap-1.5">
              {(Object.keys(PLAN_FIELD_LABEL) as (keyof PlanFields)[]).map((key) => (
                <label key={key} htmlFor={`plan-${label}-${key}`} className="flex flex-col gap-0.5">
                  <span className="text-[10px] text-fg-tertiary">{PLAN_FIELD_LABEL[key]}</span>
                  <Textarea
                    id={`plan-${label}-${key}`}
                    value={plan[key]}
                    rows={2}
                    className="bg-bg text-xs"
                    onChange={(e) => onPlanChange(label, { ...plan, [key]: e.target.value })}
                  />
                </label>
              ))}
            </div>
          ) : (
            (Object.keys(PLAN_FIELD_LABEL) as (keyof PlanFields)[]).map((key) =>
              plan[key] ? (
                <p key={key} className="text-[11px] text-fg-secondary">
                  <span className="font-medium text-fg">{PLAN_FIELD_LABEL[key]}</span> {plan[key]}
                </p>
              ) : null,
            )
          )}
        </div>
      ) : null}
    </li>
  );
}

export default function BriefPage() {
  const { id: projectId = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { user, logout } = useAuth();

  const projectQuery = useProject(projectId);
  const snapshot = useProgressSnapshot(projectId, true, { refetchInterval: 7_000 });
  const gate = snapshot.data?.pending_gate;
  const open = gate?.gate === "design_brief";
  const pendingBrief = useMemo(
    () => (open ? parseDesignBriefPayload(gate?.payload) : null),
    [open, gate?.payload],
  );
  // 게이트가 닫힌 뒤의 사후 열람 - 최신 브리프 기록(확정본)을 읽기 전용으로 보여준다.
  // 승인하고 나면 progress에서 payload가 사라져 이 화면이 빈 화면이 됐다(2026-08-15 지적).
  const recordQuery = useQuery({
    queryKey: ["design-brief", projectId],
    queryFn: () => getDesignBrief(projectId),
    enabled: !snapshot.isLoading && !open,
  });
  const resolvedBrief = useMemo(() => {
    const record = recordQuery.data;
    if (open || !record || record.status !== "resolved") return null;
    const parsed = parseDesignBriefPayload(record.payload);
    if (!parsed) return null;
    // 커밋된 계약과 같은 모습으로 - 사람 수정본(decision.ai_plan)이 AI 원안보다 우선
    const rawOverride = (record.decision as { ai_plan?: unknown } | null)?.ai_plan;
    const override = AiPlanSchema.safeParse(rawOverride ?? {});
    if (parsed.ai_plan && override.success && override.data.sections.length > 0) {
      const byLabel = new Map(
        override.data.sections.map((p) => [`${p.chapter}.${p.section}`, p] as const),
      );
      return {
        ...parsed,
        ai_plan: {
          ...parsed.ai_plan,
          sections: parsed.ai_plan.sections.map(
            (p) => byLabel.get(`${p.chapter}.${p.section}`) ?? p,
          ),
        },
      };
    }
    return parsed;
  }, [open, recordQuery.data]);
  const brief = pendingBrief ?? resolvedBrief;
  // 결정 가능(게이트 열림) vs 사후 열람(읽기 전용)의 단일 분기
  const gateOpen = open && pendingBrief != null;
  const resolvedAt = recordQuery.data?.resolved_at
    ? new Date(recordQuery.data.resolved_at).toLocaleString()
    : null;
  // 마지막 결정이 재계산이면 새 게이트가 열릴 때까지의 대기 상태다(확정 아님).
  const replanning =
    !gateOpen &&
    recordQuery.data?.status === "resolved" &&
    (recordQuery.data.decision as { action?: string } | null)?.action === "replan";

  const [editing, setEditing] = useState(false);
  const [chapters, setChapters] = useState<DraftChapter[]>([]);
  const [submitting, setSubmitting] = useState(false);
  // 절별 계획 수정본(label → fields) - 승인 시 AI 원안 대신 이것이 작성 계약으로 커밋된다.
  const [planEdits, setPlanEdits] = useState<Record<string, PlanFields>>({});
  const reviewId = gate?.review_point_id;
  // 재계산으로 게이트가 갈리면 수정본은 옛 계획 기준이라 버린다.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reviewId 변경만 감지
  useEffect(() => {
    setPlanEdits({});
  }, [reviewId]);

  const duplicatedLabels = useMemo(() => {
    const set = new Set<string>();
    for (const group of brief?.duplicate_queries ?? []) {
      for (const s of group.sections) set.add(`${s.chapter_number}.${s.section_number}`);
    }
    return set;
  }, [brief]);

  // AI 계획을 절·장 좌표로 빠르게 찾기 위한 인덱스
  const sectionPlans = useMemo(
    () =>
      new Map(
        (brief?.ai_plan?.sections ?? []).map((p) => [`${p.chapter}.${p.section}`, p] as const),
      ),
    [brief],
  );
  const chapterGoals = useMemo(
    () => new Map((brief?.ai_plan?.chapters ?? []).map((c) => [c.chapter, c.goal] as const)),
    [brief],
  );
  // 절별 수신 흐름 - 흐름은 각 관계를 받는 쪽에 한 번씩 붙여 보여준다(전달 내용의 자리).
  const incomingFlows = useMemo(() => {
    const map = new Map<string, { from: string; carries: string }[]>();
    for (const f of brief?.ai_plan?.flows ?? []) {
      const bucket = map.get(f.to);
      if (bucket) bucket.push(f);
      else map.set(f.to, [f]);
    }
    return map;
  }, [brief]);
  // 장 단위 그룹 - 절을 장 번호로 묶고 수집 질의(brief.chapters)를 헤더에 결합한다.
  const chapterGroups = useMemo(() => {
    const collect = new Map((brief?.chapters ?? []).map((c) => [c.chapter_number, c] as const));
    const groups = new Map<
      number,
      { chapter_number: number; title: string; collection_query: string; sections: BriefSection[] }
    >();
    for (const s of brief?.sections ?? []) {
      let group = groups.get(s.chapter_number);
      if (!group) {
        const info = collect.get(s.chapter_number);
        group = {
          chapter_number: s.chapter_number,
          title: info?.title || s.chapter_title,
          collection_query: info?.collection_query ?? "",
          sections: [],
        };
        groups.set(s.chapter_number, group);
      }
      group.sections.push(s);
    }
    return [...groups.values()].sort((a, b) => a.chapter_number - b.chapter_number);
  }, [brief]);

  const startEditing = () => {
    setChapters(toDraftChapters(brief?.sections ?? []));
    setEditing(true);
  };

  // 수정본이 하나라도 있으면 전체 절 계획을 결정에 실어 보낸다(부분 병합은 서버가 안 한다).
  const planOverride = useMemo(() => {
    if (!brief?.ai_plan || Object.keys(planEdits).length === 0) return undefined;
    return {
      sections: brief.ai_plan.sections.map((p) => {
        const edit = planEdits[`${p.chapter}.${p.section}`];
        return edit ? { ...p, ...edit } : p;
      }),
    };
  }, [brief, planEdits]);

  const submit = async (outline?: Outline, action?: "approve" | "replan") => {
    setSubmitting(true);
    try {
      await decideDesignBrief({
        projectId,
        outline,
        action,
        aiPlan: action === "replan" ? undefined : planOverride,
      });
      // 재조회는 기다리지 않는다 - 결정 직후 서버가 다음 단계를 여는 동안 응답이 몇 초
      // 늦을 수 있고(모델 로드 등), 그동안 화면이 멈춘 것처럼 보였다(2026-08-15 지적).
      void queryClient.invalidateQueries({ queryKey: progressKeys.snapshot(projectId) });
      void queryClient.invalidateQueries({ queryKey: ["design-brief", projectId] });
      if (action === "replan") {
        // 게이트가 새 브리프로 다시 열린다 - 이 화면에 머물며 폴링이 따라잡는다.
        toast.success("고친 목차로 계획을 다시 계산합니다.", {
          description: "잠시 후 새 계획이 이 화면에 표시됩니다.",
        });
        setEditing(false);
      } else {
        toast.success("설계를 확정했습니다.", { description: "자료 수집을 시작합니다." });
        navigate(`/projects/${projectId}/overview`);
      }
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "확정에 실패했습니다.";
      toast.error("확정 실패", { description: msg });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AppShell
      user={user ? { name: user.name, role: user.role } : null}
      onLogout={() => void logout()}
    >
      <div className="flex flex-col gap-6">
        <Button
          variant="ghost"
          size="sm"
          className="w-fit text-fg-secondary"
          onClick={() => navigate(`/projects/${projectId}/overview`)}
        >
          <ArrowLeft className="mr-1 h-4 w-4" />
          프로젝트 개요
        </Button>

        <div className="flex flex-col gap-1">
          <h1 className="text-xl font-semibold text-fg">설계 검토</h1>
          <p className="text-sm text-fg-secondary">
            {projectQuery.data?.title ?? ""}
            {brief?.message ? ` - ${brief.message}` : ""}
          </p>
        </div>

        {snapshot.isLoading || (!open && recordQuery.isLoading) ? (
          <LoadingSkeleton />
        ) : !brief ? (
          <EmptyState
            title="검토할 설계가 없습니다"
            description="실행을 시작하면 설계 브리프가 만들어집니다."
            action={
              <Button variant="outline" onClick={() => navigate(`/projects/${projectId}/overview`)}>
                개요로
              </Button>
            }
          />
        ) : editing ? (
          <div className="flex flex-col gap-4">
            <OutlineEditor
              chapters={chapters}
              onChange={setChapters}
              headerInfo={
                <span className="text-xs text-fg-secondary">
                  장 제목이 검색 질의에 함께 들어갑니다 - 장마다 다르게 쓰면 절 제목이 같아도 서로
                  다른 자료를 찾습니다.
                </span>
              }
            />
            <div className="flex items-center gap-2">
              {/* 고친 목차는 곧장 수집으로 가지 않는다 - 새 계획·경고를 다시 본 뒤 확정
                  (replan 라운드). 옛 계획으로 새 목차를 실행하는 어긋남을 막는 절차다. */}
              <Button
                onClick={() => void submit(toOutline(chapters), "replan")}
                disabled={submitting || !toOutline(chapters)}
              >
                {submitting ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
                고친 목차로 계획 다시 계산
              </Button>
              <Button variant="ghost" onClick={() => setEditing(false)} disabled={submitting}>
                취소
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            <EstimateCard brief={brief} />
            <DuplicateWarning brief={brief} />
            {!gateOpen ? (
              <div className="flex items-center gap-2 rounded-lg border border-border bg-bg-info px-4 py-3">
                {replanning ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin text-fg-tertiary" />
                ) : null}
                <p className="text-xs text-fg-secondary">
                  {replanning
                    ? "계획을 다시 계산하는 중입니다 - 잠시 후 새 계획이 이 화면에 표시됩니다."
                    : `${resolvedAt ? `${resolvedAt}에 확정된` : "확정된"} 설계입니다 - 자료 수집과 본문 작성이 아래 절별 계획대로 진행됩니다.`}
                </p>
              </div>
            ) : null}
            {gateOpen && !brief.ai_plan ? (
              <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-bg-secondary px-4 py-3">
                <p className="text-xs text-fg-secondary">
                  AI 실행 계획을 만들지 못했습니다 - 결정적 항목(질의·분량·비용)만으로 검토하거나
                  다시 계산할 수 있습니다.
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void submit(undefined, "replan")}
                  disabled={submitting}
                >
                  {submitting ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : null}
                  계획 다시 계산
                </Button>
              </div>
            ) : null}
            <FlowGraph brief={brief} />
            {brief.warnings.sections_without_analyst.length > 0 ? (
              <p className="text-xs text-fg-tertiary">
                담당 에이전트가 없는 절: {brief.warnings.sections_without_analyst.join(" · ")}
              </p>
            ) : null}

            {/* 장 단위 그룹 - 장 헤더(목표+수집 질의) 아래 그 장의 절들이 붙는다.
                수집(장마다 1회)과 절 검색이 한 위계로 보여야 20절짜리 목차가 읽힌다. */}
            <div className="rounded-lg border border-border">
              <div className="border-b border-border px-4 py-2">
                <p className="text-xs font-medium text-fg">장별 실행 계획</p>
                <p className="text-[11px] text-fg-tertiary">
                  수집 질의(장마다 1회)는 주제란 문장이 쓰이는 유일한 자리입니다. 절의 검색:은 모은
                  자료 안에서 근거를 찾는 실제 문자열입니다.
                </p>
              </div>
              {chapterGroups.map((group) => (
                <div key={group.chapter_number} className="border-b border-border last:border-b-0">
                  <div className="flex flex-col gap-1 bg-bg-secondary px-4 py-2.5">
                    <span className="text-sm font-semibold text-fg">
                      {group.chapter_number}장 {group.title || "(제목 없음)"}
                    </span>
                    {chapterGoals.get(group.chapter_number) ? (
                      <p className="text-xs text-fg-secondary">
                        {chapterGoals.get(group.chapter_number)}
                      </p>
                    ) : null}
                    {group.collection_query ? (
                      <p className="text-[11px] text-fg-tertiary">
                        수집:{" "}
                        <code className="break-all font-mono text-fg-secondary">
                          {group.collection_query}
                        </code>
                      </p>
                    ) : null}
                  </div>
                  <ul>
                    {group.sections.map((s) => (
                      <SectionRow
                        key={s.section_id}
                        section={s}
                        duplicated={duplicatedLabels.has(`${s.chapter_number}.${s.section_number}`)}
                        plan={
                          planEdits[`${s.chapter_number}.${s.section_number}`] ??
                          sectionPlans.get(`${s.chapter_number}.${s.section_number}`)
                        }
                        edited={gateOpen && `${s.chapter_number}.${s.section_number}` in planEdits}
                        onPlanChange={
                          gateOpen
                            ? (label, fields) =>
                                setPlanEdits((prev) => ({ ...prev, [label]: fields }))
                            : undefined
                        }
                        incoming={incomingFlows.get(`${s.chapter_number}.${s.section_number}`)}
                      />
                    ))}
                  </ul>
                </div>
              ))}
            </div>

            {gateOpen ? (
              <div className="flex items-center gap-2">
                <Button onClick={() => void submit()} disabled={submitting}>
                  {submitting ? (
                    <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                  ) : (
                    <ArrowRight className="mr-1 h-4 w-4" />
                  )}
                  이대로 자료 수집 시작
                </Button>
                <Button variant="outline" onClick={startEditing} disabled={submitting}>
                  <Pencil className="mr-1 h-4 w-4" />
                  목차 고치기
                </Button>
                <Button
                  variant="ghost"
                  onClick={() => void submit(undefined, "replan")}
                  disabled={submitting}
                >
                  <RefreshCw className="mr-1 h-4 w-4" />
                  계획 다시 계산
                </Button>
              </div>
            ) : null}
          </div>
        )}
      </div>
    </AppShell>
  );
}
