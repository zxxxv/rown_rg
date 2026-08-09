import { ChevronDown, ChevronRight, ChevronUp, Plus, RotateCcw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useFormContext, useWatch } from "react-hook-form";
import { useAnalysts } from "@/api/analysts";
import { type PresetDetail, usePresetDetail } from "@/api/presets";
import type { Outline } from "@/api/types";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { PromptPreviewDialog } from "./PromptPreviewDialog";
import type { ProjectFormValues } from "./schema";

// ─── 목차 설계 - 프리셋 골격을 펼쳐 장·절·에이전트를 직접 확정하는 편집기 ───
// 목차는 사람이 무조건 만든다(2026-08-03 확정): AI 목차 설계 없음. 확정된
// config.outline이 그대로 실행되고, 그 목차 순서로 자료 조사가 진행된다.

// 편집기 내부 초안 - _id는 리렌더·재정렬 안정용 클라이언트 전용 키(제출 시 제거).
interface DraftSection {
  _id: string;
  title: string;
  direction: string;
  key_points: string[];
  analysts: string[];
}

interface DraftChapter {
  _id: string;
  title: string;
  sections: DraftSection[];
}

const draftId = () => crypto.randomUUID();

function emptySection(): DraftSection {
  return { _id: draftId(), title: "", direction: "", key_points: [], analysts: [] };
}

function fromPreset(detail: PresetDetail): DraftChapter[] {
  return detail.chapters.map((ch) => ({
    _id: draftId(),
    title: ch.title,
    sections: ch.sections.map((s) => ({
      _id: draftId(),
      title: s.title,
      direction: s.direction,
      key_points: [...s.key_points],
      analysts: [...s.agents],
    })),
  }));
}

/** 제출 가능한 outline로 정리 - _id 제거, 제목 없는 절·빈 장은 버린다(백엔드 검증과 일치). */
function toOutline(chapters: DraftChapter[]): Outline | undefined {
  const cleaned = chapters
    .map((ch) => ({
      title: ch.title.trim(),
      sections: ch.sections
        .filter((s) => s.title.trim().length > 0)
        .map((s) => ({
          title: s.title.trim(),
          direction: s.direction.trim(),
          key_points: s.key_points.map((k) => k.trim()).filter(Boolean),
          analysts: s.analysts,
        })),
    }))
    .filter((ch) => ch.sections.length > 0);
  return cleaned.length > 0 ? { chapters: cleaned } : undefined;
}

function move<T>(arr: T[], index: number, delta: -1 | 1): T[] {
  const next = [...arr];
  const target = index + delta;
  if (target < 0 || target >= next.length) return next;
  const item = next[index];
  next[index] = next[target];
  next[target] = item;
  return next;
}

export function OutlineDesigner() {
  const { setValue, getValues } = useFormContext<ProjectFormValues>();
  const preset = useWatch<ProjectFormValues, "config.preset">({ name: "config.preset" });
  const detailQuery = usePresetDetail(preset ?? null);

  const [chapters, setChapters] = useState<DraftChapter[]>([]);
  // 펼쳐진 장 id 집합 - 프리셋 로드 시 전부 접힘(35섹션 프리셋 스크롤 방지),
  // 새로 추가한 장만 자동으로 펼친다.
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  const toggleChapter = (id: string) =>
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // 폼과 동기화. 목차는 필수(AI 설계 없음) - 유효한 절이 없으면 undefined가
  // 저장되고 폼 제출 단계에서 차단된다.
  const sync = useCallback(
    (next: DraftChapter[]) => {
      setChapters(next);
      setValue("config.outline", toOutline(next), { shouldDirty: true });
    },
    [setValue],
  );

  // 마운트: 기존 config.outline(수정 모드·재방문) 복원 → 없으면 프리셋 골격
  // (상세 도착 시) 또는 자유 주제 빈 장 1개로 시작. 목차 편집기는 항상 펼쳐진다.
  const mountedRef = useRef(false);
  const prevPresetRef = useRef<string | null | undefined>(undefined);
  const pendingSkeletonRef = useRef<string | null>(null);
  const startBlank = useCallback(() => {
    const blank = { _id: draftId(), title: "", sections: [emptySection()] };
    setExpandedIds(new Set([blank._id]));
    sync([blank]);
  }, [sync]);
  useEffect(() => {
    if (!mountedRef.current) {
      mountedRef.current = true;
      prevPresetRef.current = preset ?? null;
      const existing = getValues("config.outline");
      if (existing && existing.chapters.length > 0) {
        setChapters(
          existing.chapters.map((ch) => ({
            _id: draftId(),
            title: ch.title,
            sections: ch.sections.map((s) => ({ ...s, _id: draftId() })),
          })),
        );
        return;
      }
      if (preset) pendingSkeletonRef.current = preset;
      else startBlank();
      return;
    }
    if (prevPresetRef.current !== (preset ?? null)) {
      prevPresetRef.current = preset ?? null;
      if (preset) pendingSkeletonRef.current = preset;
      else startBlank();
    }
  }, [preset, getValues, startBlank]);

  // 예약된 골격 로드 - 프리셋 상세가 도착하는 시점에 편집기를 채운다(전부 접힘).
  useEffect(() => {
    if (!preset || !detailQuery.data) return;
    if (pendingSkeletonRef.current !== preset) return;
    pendingSkeletonRef.current = null;
    sync(fromPreset(detailQuery.data));
    setExpandedIds(new Set());
  }, [preset, detailQuery.data, sync]);

  const totalSections = chapters.reduce(
    (n, ch) => n + ch.sections.filter((s) => s.title.trim()).length,
    0,
  );

  if (preset && detailQuery.isLoading && chapters.length === 0) {
    return <LoadingSkeleton variant="card" count={2} />;
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2 rounded border border-accent/40 bg-bg-info p-3">
        <p className="text-xs text-fg-secondary">
          목차는 <span className="font-medium text-fg">직접 확정</span>합니다 (AI가 목차를 만들지
          않음) · 확정된 목차 순서로 자료를 조사합니다 · 유효한 절 {totalSections}개
        </p>
        <div className="flex gap-1.5">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() =>
              setExpandedIds(
                expandedIds.size === chapters.length
                  ? new Set()
                  : new Set(chapters.map((c) => c._id)),
              )
            }
          >
            {expandedIds.size === chapters.length ? (
              <ChevronUp className="mr-1 h-3.5 w-3.5" aria-hidden />
            ) : (
              <ChevronDown className="mr-1 h-3.5 w-3.5" aria-hidden />
            )}
            {expandedIds.size === chapters.length ? "모두 접기" : "모두 펼치기"}
          </Button>
          {preset ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={!detailQuery.data}
              onClick={() => {
                if (!detailQuery.data) return;
                sync(fromPreset(detailQuery.data));
                setExpandedIds(new Set());
              }}
            >
              <RotateCcw className="mr-1 h-3.5 w-3.5" aria-hidden />
              프리셋 골격으로 리셋
            </Button>
          ) : null}
        </div>
      </div>

      {chapters.map((chapter, ci) => (
        <ChapterEditor
          key={chapter._id}
          chapter={chapter}
          index={ci}
          count={chapters.length}
          expanded={expandedIds.has(chapter._id)}
          onToggle={() => toggleChapter(chapter._id)}
          onChange={(next) => sync(chapters.map((c, i) => (i === ci ? next : c)))}
          onMove={(delta) => sync(move(chapters, ci, delta))}
          onRemove={() => sync(chapters.filter((_, i) => i !== ci))}
        />
      ))}

      <Button
        type="button"
        variant="secondary"
        size="sm"
        className="w-fit"
        onClick={() => {
          const added = { _id: draftId(), title: "", sections: [emptySection()] };
          sync([...chapters, added]);
          setExpandedIds((prev) => new Set(prev).add(added._id));
        }}
      >
        <Plus className="mr-1 h-3.5 w-3.5" aria-hidden />장 추가
      </Button>
    </div>
  );
}

function ChapterEditor({
  chapter,
  index,
  count,
  expanded,
  onToggle,
  onChange,
  onMove,
  onRemove,
}: {
  chapter: DraftChapter;
  index: number;
  count: number;
  expanded: boolean;
  onToggle: () => void;
  onChange: (next: DraftChapter) => void;
  onMove: (delta: -1 | 1) => void;
  onRemove: () => void;
}) {
  const sectionCount = chapter.sections.filter((s) => s.title.trim()).length;
  return (
    <section className="flex flex-col gap-3 rounded border border-border bg-bg p-4">
      <header className="flex items-center gap-2">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 w-7 shrink-0 p-0"
          onClick={onToggle}
          aria-expanded={expanded}
          aria-label={`${index + 1}장 ${expanded ? "접기" : "펼치기"}`}
        >
          {expanded ? (
            <ChevronDown className="h-4 w-4" aria-hidden />
          ) : (
            <ChevronRight className="h-4 w-4" aria-hidden />
          )}
        </Button>
        <span className="shrink-0 rounded-sm bg-bg-secondary px-2 py-1 font-mono text-xs text-fg-secondary">
          {index + 1}장
        </span>
        <Input
          value={chapter.title}
          placeholder="장 제목 - 자료 수집은 장 단위로 한 번씩 돕니다 (예: 사업 개요)"
          onChange={(e) => onChange({ ...chapter, title: e.target.value })}
          className="h-8"
        />
        {!expanded ? (
          <span className="shrink-0 text-xs text-fg-tertiary">{sectionCount}절</span>
        ) : null}
        <RowControls
          canUp={index > 0}
          canDown={index < count - 1}
          onMove={onMove}
          onRemove={onRemove}
          label={`${index + 1}장`}
        />
      </header>

      {!expanded ? null : (
        <div className="flex flex-col gap-3 border-l-2 border-border pl-3">
          {chapter.sections.map((section, si) => (
            <SectionEditor
              key={section._id}
              section={section}
              chapterIndex={index}
              index={si}
              count={chapter.sections.length}
              onChange={(next) =>
                onChange({
                  ...chapter,
                  sections: chapter.sections.map((s, i) => (i === si ? next : s)),
                })
              }
              onMove={(delta) =>
                onChange({ ...chapter, sections: move(chapter.sections, si, delta) })
              }
              onRemove={() =>
                onChange({ ...chapter, sections: chapter.sections.filter((_, i) => i !== si) })
              }
            />
          ))}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="w-fit text-fg-secondary"
            onClick={() =>
              onChange({ ...chapter, sections: [...chapter.sections, emptySection()] })
            }
          >
            <Plus className="mr-1 h-3.5 w-3.5" aria-hidden />절 추가
          </Button>
        </div>
      )}
    </section>
  );
}

function SectionEditor({
  section,
  chapterIndex,
  index,
  count,
  onChange,
  onMove,
  onRemove,
}: {
  section: DraftSection;
  chapterIndex: number;
  index: number;
  count: number;
  onChange: (next: DraftSection) => void;
  onMove: (delta: -1 | 1) => void;
  onRemove: () => void;
}) {
  const [previewing, setPreviewing] = useState(false);
  // 규칙은 보고서 단위(config.rules)라 절 미리보기에도 같이 실어야 실제와 같아진다.
  const rules = useWatch<ProjectFormValues, "config.rules">({ name: "config.rules" }) ?? [];
  return (
    <div className="flex flex-col gap-2 rounded border border-border bg-bg-secondary p-3">
      <div className="flex items-center gap-2">
        <span className="shrink-0 font-mono text-xs text-fg-tertiary">
          {chapterIndex + 1}.{index + 1}
        </span>
        <Input
          value={section.title}
          placeholder="절 제목 - 목차·헤딩에 그대로 쓰이고 검색 질의가 됩니다"
          onChange={(e) => onChange({ ...section, title: e.target.value })}
          className="h-8 bg-bg"
        />
        <RowControls
          canUp={index > 0}
          canDown={index < count - 1}
          onMove={onMove}
          onRemove={onRemove}
          label={`${chapterIndex + 1}.${index + 1}절`}
        />
      </div>
      <p className="pl-8 text-[11px] text-fg-tertiary">
        절 제목은 목차와 본문 헤딩에 그대로 쓰이고 자료 검색의 1차 질의가 됩니다.
      </p>
      <Field hint="작성 방향은 이 절이 무엇을 논증해야 하는지 한 줄로 적는 칸이고, 검색 질의와 작성 지시에 함께 실립니다.">
        <Input
          value={section.direction}
          placeholder="예: 관련 법령과 상위 계획에 비춘 국고 지원의 당위성 논증"
          onChange={(e) => onChange({ ...section, direction: e.target.value })}
          className="h-8 bg-bg text-sm"
        />
      </Field>
      <Field hint="핵심 포인트는 반드시 다룰 항목을 줄마다 하나씩 적는 칸이고, 작성 체크리스트이자 검색 어휘로 쓰입니다.">
        <Textarea
          value={section.key_points.join("\n")}
          placeholder={"예: 관련 법률\n상위 계획 연계\n국고 지원 필요성"}
          rows={2}
          onChange={(e) => onChange({ ...section, key_points: e.target.value.split("\n") })}
          className="bg-bg text-sm"
        />
      </Field>
      <AnalystPicker
        selected={section.analysts}
        onChange={(analysts) => onChange({ ...section, analysts })}
      />
      <button
        type="button"
        className="self-start text-[11px] text-fg-tertiary underline"
        onClick={() => setPreviewing(true)}
      >
        이 절의 최종 프롬프트 미리보기
      </button>
      {previewing ? (
        <PromptPreviewDialog
          analysts={section.analysts}
          rules={rules}
          title={section.title}
          direction={section.direction}
          keyPoints={section.key_points.filter((k) => k.trim())}
          onClose={() => setPreviewing(false)}
        />
      ) : null}
    </div>
  );
}

/** 입력 칸 하나 - "무엇을 적는 칸이고 어디에 쓰이는지"를 한 줄 문장으로 붙인다.
 * 빈 상자만 늘어놓으면 무엇을 적는 칸인지 알 수 없다(사용자 지적 2026-08-10). */
function Field({ hint, children }: { hint: string; children: React.ReactNode }) {
  // label이 아니라 div - 자식이 Input/Textarea 중 무엇이든 오고, 컨트롤 id를
  // 여기서 알 수 없다(연결 없는 label은 스크린리더에 더 나쁘다).
  return (
    <div className="flex flex-col gap-1">
      <p className="text-[11px] text-fg-tertiary">{hint}</p>
      {children}
    </div>
  );
}

function RowControls({
  canUp,
  canDown,
  onMove,
  onRemove,
  label,
}: {
  canUp: boolean;
  canDown: boolean;
  onMove: (delta: -1 | 1) => void;
  onRemove: () => void;
  label: string;
}) {
  return (
    <div className="flex shrink-0 items-center gap-0.5">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-7 w-7 p-0"
        disabled={!canUp}
        onClick={() => onMove(-1)}
        aria-label={`${label} 위로`}
      >
        <ChevronUp className="h-4 w-4" aria-hidden />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-7 w-7 p-0"
        disabled={!canDown}
        onClick={() => onMove(1)}
        aria-label={`${label} 아래로`}
      >
        <ChevronDown className="h-4 w-4" aria-hidden />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-7 w-7 p-0 text-fg-danger"
        onClick={onRemove}
        aria-label={`${label} 삭제`}
      >
        <Trash2 className="h-4 w-4" aria-hidden />
      </Button>
    </div>
  );
}

function AnalystPicker({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const analystsQuery = useAnalysts();
  const analysts = analystsQuery.data ?? [];

  const toggle = (name: string) => {
    // 선택 순서를 보존한다 - 프롬프트에 그 순서로 관점이 실린다(전부 반영).
    onChange(selected.includes(name) ? selected.filter((n) => n !== name) : [...selected, name]);
  };

  return (
    <details className="group rounded border border-border bg-bg">
      <summary className="cursor-pointer select-none px-3 py-2 text-xs text-fg-secondary">
        <span className="font-medium text-fg-secondary">담당 에이전트</span>{" "}
        <span className="text-fg-tertiary">는 분석 관점과 목표 분량을 정합니다.</span>{" "}
        {selected.length > 0 ? (
          <span className="font-medium text-fg">
            {selected.join(", ")}
            <span className="ml-1 font-normal text-fg-tertiary">
              {selected.length > 1
                ? `${selected.length}개 관점을 모두 반영하고 분량·검색량을 함께 올립니다.`
                : "이 관점의 전문성과 분량 기준으로 작성합니다."}
            </span>
          </span>
        ) : (
          <span className="text-fg-tertiary">
            미배정 상태라 기본 규칙만 적용되고 분량 목표가 없습니다. 중요한 절은 2~3개 고르세요.
          </span>
        )}
      </summary>
      <div className="flex flex-wrap gap-1.5 border-t border-border p-3">
        {analystsQuery.isLoading ? (
          <p className="text-xs text-fg-tertiary">카탈로그 불러오는 중…</p>
        ) : (
          analysts.map((a) => {
            const active = selected.includes(a.name);
            const order = selected.indexOf(a.name);
            return (
              <button
                key={a.id}
                type="button"
                onClick={() => toggle(a.name)}
                title={`${a.desc}${a.pages ? ` · ${a.pages}p` : ""}`}
                aria-pressed={active}
                className={cn(
                  "rounded-full border px-2.5 py-1 text-xs transition-colors",
                  active
                    ? "border-accent bg-bg-info font-medium text-fg"
                    : "border-border bg-bg text-fg-secondary hover:border-fg-tertiary",
                )}
              >
                {active ? `${order + 1}. ` : ""}
                {a.name}
                <span className="ml-1 text-[10px] text-fg-tertiary">{a.cat}</span>
              </button>
            );
          })
        )}
      </div>
    </details>
  );
}
