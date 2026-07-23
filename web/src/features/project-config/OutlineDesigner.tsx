import {
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Plus,
  RotateCcw,
  Sparkles,
  Trash2,
  Wrench,
} from "lucide-react";
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
import type { ProjectFormValues } from "./schema";

// ─── 목차 설계 — 프리셋 골격을 펼쳐 장·절·에이전트를 직접 확정하는 편집기 ───
// 확정된 목차(config.outline)는 백엔드 planner LLM을 우회해 그대로 실행된다.
// "AI 자동 설계"로 두면 outline을 보내지 않아 기존(LLM 설계) 경로로 동작한다.

type Mode = "auto" | "manual";

// 편집기 내부 초안 — _id는 리렌더·재정렬 안정용 클라이언트 전용 키(제출 시 제거).
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

/** 제출 가능한 outline로 정리 — _id 제거, 제목 없는 절·빈 장은 버린다(백엔드 검증과 일치). */
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

  const [mode, setMode] = useState<Mode>("auto");
  const [chapters, setChapters] = useState<DraftChapter[]>([]);
  // 펼쳐진 장 id 집합 — 프리셋 로드 시 전부 접힘(35섹션 프리셋 스크롤 방지),
  // 새로 추가한 장만 자동으로 펼친다.
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());

  const toggleChapter = (id: string) =>
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // 폼과 동기화 — 유효한 절이 하나도 없으면 outline을 보내지 않는다(AI 설계로 폴백).
  const sync = useCallback(
    (next: DraftChapter[], nextMode: Mode) => {
      setChapters(next);
      setMode(nextMode);
      setValue("config.outline", nextMode === "manual" ? toOutline(next) : undefined, {
        shouldDirty: true,
      });
    },
    [setValue],
  );

  // 마운트: 기존 config.outline(수정 모드·재방문)이 있으면 그대로 편집기로 복원.
  // 이후 흐름은 기본 auto(AI 설계) — 프리셋을 골라도 "직접 설계하기"를 눌러야
  // 골격이 펼쳐진다(처음 화면을 짧게 유지).
  const mountedRef = useRef(false);
  const prevPresetRef = useRef<string | null | undefined>(undefined);
  // 상세 로딩 전에 "직접 설계"를 누르거나 편집 중 프리셋을 바꾼 경우 —
  // 해당 프리셋 상세가 도착하면 골격을 로드하도록 예약한다.
  const pendingSkeletonRef = useRef<string | null>(null);
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
        setMode("manual");
      }
      return;
    }
    if (prevPresetRef.current !== (preset ?? null)) {
      prevPresetRef.current = preset ?? null;
      if (mode === "manual") {
        // 편집 중 유형 변경: 새 프리셋 골격으로 리셋(도착 시), 자유 주제면 AI 설계로.
        if (preset) pendingSkeletonRef.current = preset;
        else sync([], "auto");
      }
    }
  }, [preset, getValues, sync, mode]);

  // 예약된 골격 로드 — 프리셋 상세가 도착하는 시점에 편집기를 채운다.
  useEffect(() => {
    if (!preset || !detailQuery.data) return;
    if (pendingSkeletonRef.current !== preset) return;
    pendingSkeletonRef.current = null;
    sync(fromPreset(detailQuery.data), "manual");
    setExpandedIds(new Set());
  }, [preset, detailQuery.data, sync]);

  const totalSections = chapters.reduce(
    (n, ch) => n + ch.sections.filter((s) => s.title.trim()).length,
    0,
  );

  if (mode === "auto") {
    return (
      <div className="flex flex-col gap-3 rounded border border-border bg-bg p-4">
        <div className="flex items-start gap-2">
          <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
          <p className="text-sm text-fg-secondary">
            {preset
              ? "AI가 이 유형의 표준 골격을 주제에 맞게 다듬어 목차를 설계합니다. 그대로 시작해도 됩니다."
              : "AI가 주제에 맞는 목차를 새로 설계합니다. 그대로 시작해도 됩니다."}
          </p>
        </div>
        <div>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => {
              if (!preset) {
                const blank = { _id: draftId(), title: "", sections: [emptySection()] };
                sync([blank], "manual");
                setExpandedIds(new Set([blank._id]));
              } else if (detailQuery.data) {
                sync(fromPreset(detailQuery.data), "manual");
                setExpandedIds(new Set());
              } else {
                // 상세 로딩 전 — 도착 시 골격 로드 예약 (그동안 스켈레톤 표시)
                pendingSkeletonRef.current = preset;
                sync([], "manual");
              }
            }}
          >
            <Wrench className="mr-1 h-3.5 w-3.5" aria-hidden />
            목차 직접 설계·에이전트 배정
          </Button>
        </div>
      </div>
    );
  }

  if (preset && detailQuery.isLoading && chapters.length === 0) {
    return <LoadingSkeleton variant="card" count={2} />;
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2 rounded border border-accent/40 bg-bg-info p-3">
        <p className="text-xs text-fg-secondary">
          이 목차가 <span className="font-medium text-fg">그대로 실행</span>됩니다 (AI가 수정하지
          않음) · 유효한 절 {totalSections}개
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
                sync(fromPreset(detailQuery.data), "manual");
                setExpandedIds(new Set());
              }}
            >
              <RotateCcw className="mr-1 h-3.5 w-3.5" aria-hidden />
              프리셋 골격으로 리셋
            </Button>
          ) : null}
          <Button type="button" variant="ghost" size="sm" onClick={() => sync([], "auto")}>
            <Sparkles className="mr-1 h-3.5 w-3.5" aria-hidden />
            AI 설계에 맡기기
          </Button>
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
          onChange={(next) =>
            sync(
              chapters.map((c, i) => (i === ci ? next : c)),
              "manual",
            )
          }
          onMove={(delta) => sync(move(chapters, ci, delta), "manual")}
          onRemove={() =>
            sync(
              chapters.filter((_, i) => i !== ci),
              "manual",
            )
          }
        />
      ))}

      <Button
        type="button"
        variant="secondary"
        size="sm"
        className="w-fit"
        onClick={() => {
          const added = { _id: draftId(), title: "", sections: [emptySection()] };
          sync([...chapters, added], "manual");
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
          placeholder="장 제목 (예: 사업 개요)"
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
  return (
    <div className="flex flex-col gap-2 rounded border border-border bg-bg-secondary p-3">
      <div className="flex items-center gap-2">
        <span className="shrink-0 font-mono text-xs text-fg-tertiary">
          {chapterIndex + 1}.{index + 1}
        </span>
        <Input
          value={section.title}
          placeholder="절 제목 (검색 질의로 쓸 수 있게 구체적으로)"
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
      <Input
        value={section.direction}
        placeholder="작성 방향 (선택)"
        onChange={(e) => onChange({ ...section, direction: e.target.value })}
        className="h-8 bg-bg text-sm"
      />
      <Textarea
        value={section.key_points.join("\n")}
        placeholder="핵심 포인트 — 줄마다 하나 (선택)"
        rows={2}
        onChange={(e) => onChange({ ...section, key_points: e.target.value.split("\n") })}
        className="bg-bg text-sm"
      />
      <AnalystPicker
        selected={section.analysts}
        onChange={(analysts) => onChange({ ...section, analysts })}
      />
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
    // 선택 순서를 보존한다 — 첫 번째가 대표 에이전트(페르소나·분량 기준).
    onChange(selected.includes(name) ? selected.filter((n) => n !== name) : [...selected, name]);
  };

  return (
    <details className="group rounded border border-border bg-bg">
      <summary className="cursor-pointer select-none px-3 py-2 text-xs text-fg-secondary">
        담당 에이전트{" "}
        {selected.length > 0 ? (
          <span className="font-medium text-fg">
            {selected.join(", ")}
            <span className="ml-1 text-fg-tertiary">(첫 번째가 대표)</span>
          </span>
        ) : (
          <span className="text-fg-tertiary">미배정 — 기본 작성 규칙만 적용</span>
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
                {active && order === 0 ? "★ " : ""}
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
