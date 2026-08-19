import { ChevronDown, ChevronRight, ChevronUp, Copy, Plus, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { useAnalysts } from "@/api/analysts";
import type { PresetChapterDetail, PresetDetail } from "@/api/presets";
import type { Outline } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { PromptDialog } from "@/features/prompts/PromptDialog";
import { cn } from "@/lib/utils";
import { PromptPreviewDialog } from "./PromptPreviewDialog";
import { duplicatedSectionKeys, duplicateQueryGroups, effectiveSearchQuery } from "./searchPreview";

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
  /** 의존 계약("4.1"|"4.1(지표)"|"4.*") - 앞 절 확정값을 받아 쓴다(사실 대장 주입) */
  builds_on: string[];
}

export interface DraftChapter {
  _id: string;
  title: string;
  sections: DraftSection[];
}

export const draftId = () => crypto.randomUUID();

export function emptySection(): DraftSection {
  return { _id: draftId(), title: "", direction: "", key_points: [], analysts: [], builds_on: [] };
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
      builds_on: [...s.builds_on],
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
          builds_on: s.builds_on.map((b) => b.trim()).filter(Boolean),
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
          builds_on: s.builds_on.map((b) => b.trim()).filter(Boolean),
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

  // 같은 질의를 쓰는 절 - 짜는 동안 바로 알려준다. 실행 후 브리프 게이트에서도 다시
  // 확인하지만, 그때는 이미 만들고 난 뒤다.
  const duplicates = useMemo(() => duplicateQueryGroups(chapters), [chapters]);
  const duplicatedKeys = useMemo(() => duplicatedSectionKeys(duplicates), [duplicates]);

  return (
    <div className="flex flex-col gap-4">
      {duplicates.length > 0 ? (
        <div className="flex flex-col gap-2 rounded border border-border-danger bg-bg-danger-subtle p-3">
          <p className="text-xs font-medium text-fg-danger">
            검색 질의가 겹치는 절이 있습니다 - 이 절들은 같은 자료를 인용하게 됩니다
          </p>
          <p className="text-[11px] text-fg-secondary">
            검색은 질의가 같으면 결과도 같습니다. 장 제목을 서로 다르게 적거나 절 제목에 대상을
            넣으면 갈라집니다.
          </p>
          <ul className="flex flex-col gap-1">
            {duplicates.map((group) => (
              <li key={group.query} className="text-[11px]">
                <code className="font-mono text-fg">{group.query}</code>
                <span className="text-fg-secondary">
                  {" "}
                  - {group.sections.map((s) => s.label).join(" · ")}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
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
          duplicatedKeys={duplicatedKeys}
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
  duplicatedKeys,
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
  /** 중복 질의에 걸린 절 좌표("ci:si") - 해당 절만 표시를 바꾼다 */
  duplicatedKeys: Set<string>;
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
          placeholder="장 제목 - 자료 수집 단위이자 아래 절들의 검색 질의에 함께 들어갑니다"
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
              chapterTitle={chapter.title}
              duplicated={duplicatedKeys.has(`${index}:${si}`)}
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
  chapterTitle,
  duplicated,
  onChange,
  onMove,
  onRemove,
}: {
  section: DraftSection;
  chapterIndex: number;
  index: number;
  count: number;
  previewRules: string[];
  /** 이 절이 속한 장 제목 - 검색 질의 미리보기에 결합된다 */
  chapterTitle: string;
  /** 다른 절과 질의가 겹치는가 - 겹치면 미리보기를 경고색으로 */
  duplicated: boolean;
  onChange: (next: DraftSection) => void;
  onMove: (delta: -1 | 1) => void;
  onRemove: () => void;
}) {
  const [previewing, setPreviewing] = useState(false);
  const query = effectiveSearchQuery(chapterTitle, section.title);
  return (
    <div className="flex flex-col gap-2 rounded border border-border bg-bg-secondary p-3">
      <div className="flex items-center gap-2">
        <span className="shrink-0 font-mono text-xs text-fg-tertiary">
          {chapterIndex + 1}.{index + 1}
        </span>
        <Input
          value={section.title}
          placeholder="절 제목 - 목차·헤딩에 그대로 실립니다"
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
      {/* 이 절이 실제로 무엇으로 검색되는지 - 장 제목이 함께 들어간다는 사실을
          설명문으로 말하는 대신 결과를 보여준다. 확정 값은 실행 후 설계 검토 화면이
          서버에서 계산해 다시 보여준다. */}
      {query ? (
        <p className="pl-8 text-[11px] text-fg-tertiary">
          검색:{" "}
          <code className={cn("font-mono", duplicated ? "text-fg-danger" : "text-fg-secondary")}>
            {query}
          </code>
          {duplicated ? <span className="text-fg-danger"> - 다른 절과 겹칩니다</span> : null}
        </p>
      ) : null}
      <Field hint="작성 방향 - 이 절이 무엇을 논증해야 하는지 한 줄로">
        <Input
          value={section.direction}
          placeholder="예: 관련 법령과 상위 계획에 비춘 국고 지원의 당위성 논증"
          onChange={(e) => onChange({ ...section, direction: e.target.value })}
          className="h-8 bg-bg text-sm"
        />
      </Field>
      <Field hint="핵심 포인트 - 반드시 다룰 항목을 줄마다 하나씩(작성 체크리스트 겸 검색 어휘)">
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
      <Field hint="앞 절 이어받기(선택) - 이어받을 절 번호를 쉼표로. 예: 6.2 또는 6.2(총사업비), 장 전체는 2.* (그 절의 확정 수치를 그대로 받아 다시 찾지 않습니다)">
        <Input
          value={section.builds_on.join(", ")}
          placeholder="예: 6.2(총사업비), 2.*"
          onChange={(e) =>
            onChange({
              ...section,
              builds_on: e.target.value
                .split(",")
                .map((b) => b.trim())
                .filter(Boolean),
            })
          }
          className="h-8 bg-bg font-mono text-sm"
        />
      </Field>
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
                title={[
                  a.shared && a.owner_name ? `${a.owner_name} 공개` : "",
                  a.desc,
                  a.pages ? `${a.pages}p` : "",
                ]
                  .filter(Boolean)
                  .join(" · ")}
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
                {/* 남이 공개한 것임을 칩에서 바로 알린다 - 이름만 보면 내 것과
                    구분이 안 되는데, 주인이 고치면 다음 실행부터 글이 달라진다. */}
                {a.shared ? (
                  <span className="ml-1 text-[10px] text-accent">
                    공유{a.owner_name ? ` · ${a.owner_name}` : ""}
                  </span>
                ) : null}
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
