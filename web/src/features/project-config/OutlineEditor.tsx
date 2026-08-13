import { ChevronDown, ChevronRight, ChevronUp, Copy, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useAnalysts } from "@/api/analysts";
import type { PresetChapterDetail, PresetDetail } from "@/api/presets";
import type { Outline } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { PromptDialog } from "@/features/prompts/PromptDialog";
import { cn } from "@/lib/utils";
import { PromptPreviewDialog } from "./PromptPreviewDialog";

// ─── 목차 편집기(순수) - 장·절·에이전트 배정을 value/onChange로 편집한다 ───
// 프로젝트 생성 폼(OutlineDesigner)과 내 프리셋 관리가 같은 편집기를 쓴다.
// 편집기가 둘로 갈리면 반드시 어긋나므로, 폼 결합(react-hook-form)은 전부
// 래퍼 몫이고 여기는 순수 상태만 다룬다.

// 편집기 내부 초안 - _id는 리렌더·재정렬 안정용 클라이언트 전용 키(제출 시 제거).
export interface DraftSection {
  _id: string;
  title: string;
  direction: string;
  key_points: string[];
  analysts: string[];
}

export interface DraftChapter {
  _id: string;
  title: string;
  sections: DraftSection[];
}

export const draftId = () => crypto.randomUUID();

export function emptySection(): DraftSection {
  return { _id: draftId(), title: "", direction: "", key_points: [], analysts: [] };
}

export function emptyChapter(): DraftChapter {
  return { _id: draftId(), title: "", sections: [emptySection()] };
}

export function fromPreset(detail: PresetDetail): DraftChapter[] {
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
export function toOutline(chapters: DraftChapter[]): Outline | undefined {
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

/** 내 프리셋 저장용 chapters - 프리셋 상세와 같은 와이어 모양(analysts → agents). */
export function toPresetChapters(chapters: DraftChapter[]): PresetChapterDetail[] {
  return chapters
    .map((ch) => ({
      title: ch.title.trim(),
      sections: ch.sections
        .filter((s) => s.title.trim().length > 0)
        .map((s) => ({
          title: s.title.trim(),
          direction: s.direction.trim(),
          key_points: s.key_points.map((k) => k.trim()).filter(Boolean),
          agents: s.analysts,
        })),
    }))
    .filter((ch) => ch.sections.length > 0);
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

export interface OutlineEditorProps {
  chapters: DraftChapter[];
  onChange: (next: DraftChapter[]) => void;
  /** 헤더 왼쪽 안내문 - 쓰는 화면마다 다르다 */
  headerInfo: React.ReactNode;
  /** 헤더 오른쪽 추가 액션(프리셋 리셋·프리셋 저장 등) */
  headerActions?: React.ReactNode;
  /** 절 프롬프트 미리보기에 실을 작성 규칙(UUID) - 생성 폼 밖에서는 생략 */
  previewRules?: string[];
  /** 마운트 시 펼침 상태 - 빈 목차로 시작하면 "all", 골격 로드면 "none".
   * 래퍼가 골격을 통째로 갈아끼울 때는 key를 바꿔 리마운트한다. */
  defaultExpanded?: "all" | "none";
}

export function OutlineEditor({
  chapters,
  onChange,
  headerInfo,
  headerActions,
  previewRules = [],
  defaultExpanded = "none",
}: OutlineEditorProps) {
  // 펼쳐진 장 id 집합 - 프리셋 로드 시 전부 접힘(35섹션 프리셋 스크롤 방지),
  // 새로 추가·복사한 장만 자동으로 펼친다.
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() =>
    defaultExpanded === "all" ? new Set(chapters.map((c) => c._id)) : new Set(),
  );

  const toggleChapter = (id: string) =>
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2 rounded border border-accent/40 bg-bg-info p-3">
        <p className="text-xs text-fg-secondary">{headerInfo}</p>
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
          {headerActions}
        </div>
      </div>

      {chapters.map((chapter, ci) => (
        <ChapterEditor
          key={chapter._id}
          chapter={chapter}
          index={ci}
          count={chapters.length}
          expanded={expandedIds.has(chapter._id)}
          previewRules={previewRules}
          onToggle={() => toggleChapter(chapter._id)}
          onChange={(next) => onChange(chapters.map((c, i) => (i === ci ? next : c)))}
          onMove={(delta) => onChange(move(chapters, ci, delta))}
          onRemove={() => onChange(chapters.filter((_, i) => i !== ci))}
          onDuplicate={() => {
            // 같은 구성으로 여러 대상을 분석하는 용례(정책 A/B/C) - 절 구성·방향·
            // 에이전트 배정까지 통째로 복제해 바로 아래에 넣는다(2026-08-12 QA 요청).
            const dup: DraftChapter = {
              _id: draftId(),
              title: chapter.title.trim() ? `${chapter.title} (복사)` : "",
              sections: chapter.sections.map((s) => ({ ...s, _id: draftId() })),
            };
            onChange([...chapters.slice(0, ci + 1), dup, ...chapters.slice(ci + 1)]);
            setExpandedIds((prev) => new Set(prev).add(dup._id));
          }}
        />
      ))}

      <Button
        type="button"
        variant="secondary"
        size="sm"
        className="w-fit"
        onClick={() => {
          const added = emptyChapter();
          onChange([...chapters, added]);
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
  previewRules,
  onToggle,
  onChange,
  onMove,
  onRemove,
  onDuplicate,
}: {
  chapter: DraftChapter;
  index: number;
  count: number;
  expanded: boolean;
  previewRules: string[];
  onToggle: () => void;
  onChange: (next: DraftChapter) => void;
  onMove: (delta: -1 | 1) => void;
  onRemove: () => void;
  onDuplicate: () => void;
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
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 w-7 shrink-0 p-0"
          onClick={onDuplicate}
          aria-label={`${index + 1}장 복사`}
          title="이 장을 절 구성 그대로 복사해 아래에 추가"
        >
          <Copy className="h-3.5 w-3.5" aria-hidden />
        </Button>
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
              previewRules={previewRules}
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
  previewRules,
  onChange,
  onMove,
  onRemove,
}: {
  section: DraftSection;
  chapterIndex: number;
  index: number;
  count: number;
  previewRules: string[];
  onChange: (next: DraftSection) => void;
  onMove: (delta: -1 | 1) => void;
  onRemove: () => void;
}) {
  const [previewing, setPreviewing] = useState(false);
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
          rules={previewRules}
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
  // 목차를 짜다 원하는 관점이 없을 때 여기서 바로 만든다 - 프롬프트 화면으로
  // 나갔다 오면 동선이 끊긴다(초안은 남지만 흐름이 끊기는 건 마찬가지).
  const [creating, setCreating] = useState(false);

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
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="rounded-full border border-dashed border-fg-tertiary px-2.5 py-1 text-xs text-fg-secondary hover:border-accent hover:text-fg"
        >
          + 새 에이전트
        </button>
      </div>
      {creating ? (
        <PromptDialog
          kind="agent"
          onClose={() => setCreating(false)}
          onSaved={(p) => {
            // 만든 즉시 이 절에 배정 - 목록 갱신은 캐시 무효화가 처리한다.
            if (!selected.includes(p.name)) onChange([...selected, p.name]);
            setCreating(false);
          }}
        />
      ) : null}
    </details>
  );
}
