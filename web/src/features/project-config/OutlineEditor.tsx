import {
  AlertCircle,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Copy,
  GripVertical,
  Plus,
  Trash2,
  TriangleAlert,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { type Analyst, useAnalysts } from "@/api/analysts";
import type { PresetChapterDetail, PresetDetail } from "@/api/presets";
import type { Outline } from "@/api/types";
import { Button } from "@/components/ui/button";
import { HelpTip } from "@/components/ui/help-tip";
import { Input } from "@/components/ui/input";
import { PromptDialog } from "@/features/prompts/PromptDialog";
import { cn } from "@/lib/utils";
import { BuildsOnPicker } from "./BuildsOnPicker";
import { PromptPreviewDialog } from "./PromptPreviewDialog";
import {
  analystQueryPreviews,
  duplicatedSectionKeys,
  duplicateQueryGroups,
  effectiveSearchQuery,
} from "./searchPreview";
import {
  chapterIssueCounts,
  collectOutlineIssues,
  type FormIssue,
  formatRefLabel,
  indexOutlineIssues,
  LIMITS,
  type OutlineField,
  parseRefLabel,
} from "./validation";

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

/** 체크리스트에서 "이동"을 눌렀을 때 편집기가 데려갈 자리.
 * nonce: 같은 자리를 다시 눌러도 다시 이동해야 하므로 매번 새 값을 준다. */
export interface OutlineFocusTarget {
  chapterIndex: number;
  sectionIndex: number | null;
  field: OutlineField;
  nonce: number;
}

/** 칸마다 붙는 DOM id - 이동·포커스의 유일한 좌표계. */
export function outlineElementId(target: {
  chapterIndex: number;
  sectionIndex: number | null;
  field: OutlineField;
}): string {
  const scope = target.sectionIndex === null ? "c" : `s${target.sectionIndex}`;
  return `oe-${target.chapterIndex}-${scope}-${target.field}`;
}

/** 문제 있는 칸의 테두리 - 못 채운 곳은 빨강, 확인만 필요한 곳은 주황. */
function issueRing(issue: FormIssue | undefined): string {
  if (!issue) return "";
  return issue.level === "blocker"
    ? "border-fg-danger focus-visible:ring-fg-danger"
    : "border-fg-warning";
}

/** 그 칸 바로 아래 한 줄 사유 - 왜 빨간지 그 자리에서 말한다. */
function IssueNote({ issue }: { issue: FormIssue | undefined }) {
  if (!issue) return null;
  return (
    <p
      className={cn(
        "flex items-start gap-1 text-[11px]",
        issue.level === "blocker" ? "text-fg-danger" : "text-fg-warning",
      )}
    >
      {issue.level === "blocker" ? (
        <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
      ) : (
        <TriangleAlert className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
      )}
      <span>{issue.message}</span>
    </p>
  );
}

export const draftId = () => crypto.randomUUID();

export function emptySection(): DraftSection {
  // key_points에 빈 줄 하나를 두고 시작한다 - 필수 항목인데 빈 목록이면 "추가" 단추만
  // 보여 어디에 적는지 알 수 없다(2026-08-25).
  return {
    _id: draftId(),
    title: "",
    direction: "",
    key_points: [""],
    analysts: [],
    builds_on: [],
  };
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

/** builds_on id 토큰("s:<uuid>"|"c:<uuid>") → 현재 번호 표시("4.1"|"4.*").
 * 번호 문자열은 그대로 통과, 대상이 사라진 토큰은 null(의존도 함께 사라진 것). */
function tokenToLabel(
  raw: string,
  secLabelById: Map<string, string>,
  chNumById: Map<string, string>,
): string | null {
  const s = /^\s*s:([0-9a-fA-F-]{36})(?:\(\s*([^()]+?)\s*\))?\s*$/.exec(raw);
  if (s) {
    const label = secLabelById.get(s[1].toLowerCase());
    if (!label) return null;
    return s[2] ? `${label}(${s[2]})` : label;
  }
  const c = /^\s*c:([0-9a-fA-F-]{36})(?:\.\*)?\s*$/.exec(raw);
  if (c) {
    const num = chNumById.get(c[1].toLowerCase());
    return num ? `${num}.*` : null;
  }
  return raw;
}

/** 저장된 outline → 편집기 초안. 서버 발급 id를 _id로 잇는다(정체성 왕복의 반쪽).
 * builds_on의 id 토큰은 현재 번호로 표시 변환한다 - 편집기 안은 항상 번호이고,
 * 번호→토큰 재해석은 저장 시점에 서버가 한다. */
export function fromOutline(outline: Outline): DraftChapter[] {
  const secLabelById = new Map<string, string>();
  const chNumById = new Map<string, string>();
  outline.chapters.forEach((ch, ci) => {
    if (ch.id) chNumById.set(ch.id.toLowerCase(), String(ci + 1));
    ch.sections.forEach((s, si) => {
      if (s.id) secLabelById.set(s.id.toLowerCase(), `${ci + 1}.${si + 1}`);
    });
  });
  return outline.chapters.map((ch) => ({
    _id: ch.id ?? draftId(),
    title: ch.title,
    sections: ch.sections.map((s) => ({
      _id: s.id ?? draftId(),
      title: s.title,
      direction: s.direction,
      key_points: [...s.key_points],
      analysts: [...s.analysts],
      builds_on: s.builds_on
        .map((b) => tokenToLabel(b, secLabelById, chNumById))
        .filter((b): b is string => b !== null),
    })),
  }));
}

/** 제출 가능한 outline로 정리 - _id는 안정 id로 실어 보내고(정체성 왕복),
 * 제목 없는 절·빈 장은 버린다(백엔드 검증과 일치). */
export function toOutline(chapters: DraftChapter[]): Outline | undefined {
  const cleaned = chapters
    .map((ch) => ({
      id: ch._id,
      title: ch.title.trim(),
      sections: ch.sections
        .filter((s) => s.title.trim().length > 0)
        .map((s) => ({
          id: s._id,
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

// ─── 이어받기는 번호로 저장되고 번호는 위치에서 나온다 ───
// 그래서 절을 옮기거나 지우면 "4.1"이 **말없이 다른 절**을 가리키게 된다(드래그를
// 붙이면서 이 사고가 한 번의 손짓으로 일어난다). 구조가 바뀔 때마다 번호를 절
// 정체성(_id)으로 되짚어 다시 매긴다 - 사람이 고른 것은 "그 절"이지 "그 번호"가 아니다.

/** 위치 → 표시 번호. 편집기 안에서는 배열 위치가 곧 번호다(제목 없는 절도 한 칸 차지). */
function refIndex(chapters: DraftChapter[]): {
  idByLabel: Map<string, string>;
  chapterIdByNum: Map<string, string>;
  labelById: Map<string, string>;
  numByChapterId: Map<string, string>;
} {
  const idByLabel = new Map<string, string>();
  const chapterIdByNum = new Map<string, string>();
  const labelById = new Map<string, string>();
  const numByChapterId = new Map<string, string>();
  chapters.forEach((ch, ci) => {
    chapterIdByNum.set(String(ci + 1), ch._id);
    numByChapterId.set(ch._id, String(ci + 1));
    ch.sections.forEach((s, si) => {
      const label = `${ci + 1}.${si + 1}`;
      idByLabel.set(label, s._id);
      labelById.set(s._id, label);
    });
  });
  return { idByLabel, chapterIdByNum, labelById, numByChapterId };
}

/** 구조가 바뀐 뒤 builds_on 번호를 다시 매긴다(before의 번호 → 절 _id → after의 번호).
 * 대상이 사라졌으면 원래 표기를 남긴다 - 조용히 지우면 의존이 사라진 줄 모른다
 * (검증이 "없는 절을 가리킵니다"로 잡아 준다). 순수 함수. */
export function withRemappedRefs(before: DraftChapter[], after: DraftChapter[]): DraftChapter[] {
  const from = refIndex(before);
  const to = refIndex(after);
  let changed = false;
  const remapped = after.map((ch) => ({
    ...ch,
    sections: ch.sections.map((s) => {
      if (s.builds_on.length === 0) return s;
      const next = s.builds_on.map((raw) => {
        const ref = parseRefLabel(raw);
        if (!ref) return raw;
        if (ref.section === null) {
          const chId = from.chapterIdByNum.get(String(ref.chapter));
          const num = chId ? to.numByChapterId.get(chId) : undefined;
          return num ? `${num}.*` : raw;
        }
        const secId = from.idByLabel.get(`${ref.chapter}.${ref.section}`);
        const label = secId ? to.labelById.get(secId) : undefined;
        if (!label) return raw;
        const [chapter, section] = label.split(".");
        return formatRefLabel({
          chapter: Number(chapter),
          section: Number(section),
          metric: ref.metric,
        });
      });
      if (next.every((v, i) => v === s.builds_on[i])) return s;
      changed = true;
      return { ...s, builds_on: next };
    }),
  }));
  return changed ? remapped : after;
}

/** 절을 다른 자리로(다른 장으로도) 옮긴 새 목차. to.si는 **옮긴 뒤** 들어갈 자리. */
export function moveSectionTo(
  chapters: DraftChapter[],
  from: { ci: number; si: number },
  to: { ci: number; si: number },
): DraftChapter[] {
  const moving = chapters[from.ci]?.sections[from.si];
  if (!moving) return chapters;
  const next = chapters.map((ch) => ({ ...ch, sections: [...ch.sections] }));
  next[from.ci].sections.splice(from.si, 1);
  // 같은 장 안에서 앞으로 당겨 온 만큼 목표 자리도 당겨진다.
  const si = from.ci === to.ci && from.si < to.si ? to.si - 1 : to.si;
  next[to.ci].sections.splice(Math.max(0, Math.min(si, next[to.ci].sections.length)), 0, moving);
  return next;
}

/** 장을 다른 자리로 옮긴 새 목차. to는 **옮긴 뒤** 들어갈 자리. */
export function moveChapterTo(chapters: DraftChapter[], from: number, to: number): DraftChapter[] {
  const moving = chapters[from];
  if (!moving) return chapters;
  const rest = chapters.filter((_, i) => i !== from);
  const at = from < to ? to - 1 : to;
  return [...rest.slice(0, at), moving, ...rest.slice(at)];
}

/** 드래그 중인 것 - 절이면 sectionIndex가 있다. */
interface DragItem {
  chapterIndex: number;
  sectionIndex: number | null;
}

/** 드롭될 자리 표시 - 그 행의 위/아래 어디에 꽂히는지. */
interface DropHint {
  chapterIndex: number;
  /** null = 장 사이 */
  sectionIndex: number | null;
  position: "before" | "after";
}

/** 마우스가 행의 위쪽 절반인가 - 위/아래 어디에 꽂을지 정한다. */
function dropPosition(e: React.DragEvent, el: HTMLElement): "before" | "after" {
  const rect = el.getBoundingClientRect();
  return e.clientY < rect.top + rect.height / 2 ? "before" : "after";
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
  /** 체크리스트에서 온 이동 요청 - 그 장을 펼치고 해당 칸으로 스크롤·포커스한다 */
  focusTarget?: OutlineFocusTarget | null;
  /** 이동을 처리했음 - 래퍼가 요청을 비운다 */
  onFocusHandled?: () => void;
  /** 목차에서 찾은 문제를 래퍼(생성 폼 체크리스트)에 올려 준다 */
  onIssuesChange?: (issues: FormIssue[]) => void;
}

export function OutlineEditor({
  chapters,
  onChange,
  headerInfo,
  headerActions,
  previewRules = [],
  defaultExpanded = "none",
  focusTarget = null,
  onFocusHandled,
  onIssuesChange,
}: OutlineEditorProps) {
  // 펼쳐진 장 id 집합 - 프리셋 로드 시 전부 접힘(35섹션 프리셋 스크롤 방지),
  // 새로 추가·복사한 장만 자동으로 펼친다.
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() =>
    defaultExpanded === "all" ? new Set(chapters.map((c) => c._id)) : new Set(),
  );

  // 펼쳐진 **절** id 집합 - 접힌 절은 한 줄(번호·제목·에이전트·문제 배지)이라
  // 35절 프리셋도 한 화면에 들어온다. 새로 추가한 절만 자동으로 펼친다(2026-08-25).
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set());

  const toggleChapter = (id: string) =>
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const toggleSection = (id: string) =>
    setExpandedSections((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // 구조가 바뀌는 모든 변경은 여기를 지난다 - builds_on 번호를 절 정체성으로 다시
  // 매겨서, 옮기거나 지운 뒤에도 사람이 고른 "그 절"을 계속 가리키게 한다.
  const emit = (next: DraftChapter[]) => onChange(withRemappedRefs(chapters, next));

  // ─── 드래그로 순서 바꾸기 ───
  // 라이브러리 없이 HTML5 드래그로 한다(의존성 0). 손잡이만 draggable이라 입력칸
  // 안의 글자 선택은 그대로 되고, ↑↓ 단추는 남긴다 - 터치·키보드에는 그쪽이 낫다.
  const [drag, setDrag] = useState<DragItem | null>(null);
  const [dropHint, setDropHint] = useState<DropHint | null>(null);

  const endDrag = () => {
    setDrag(null);
    setDropHint(null);
  };

  /** 절을 놓았다 - 힌트가 가리키는 자리에 꽂는다(다른 장으로도 간다). */
  const dropSection = (hint: DropHint) => {
    if (!drag || drag.sectionIndex === null) return;
    const target =
      hint.sectionIndex === null
        ? { ci: hint.chapterIndex, si: chapters[hint.chapterIndex]?.sections.length ?? 0 }
        : {
            ci: hint.chapterIndex,
            si: hint.position === "before" ? hint.sectionIndex : hint.sectionIndex + 1,
          };
    const from = { ci: drag.chapterIndex, si: drag.sectionIndex };
    if (from.ci === target.ci && (from.si === target.si || from.si + 1 === target.si)) {
      endDrag();
      return; // 제자리
    }
    emit(moveSectionTo(chapters, from, target));
    endDrag();
  };

  /** 장을 놓았다. */
  const dropChapter = (hint: DropHint) => {
    if (!drag || drag.sectionIndex !== null) return;
    const to = hint.position === "before" ? hint.chapterIndex : hint.chapterIndex + 1;
    if (drag.chapterIndex === to || drag.chapterIndex + 1 === to) {
      endDrag();
      return;
    }
    emit(moveChapterTo(chapters, drag.chapterIndex, to));
    endDrag();
  };

  // 같은 질의를 쓰는 절 - 짜는 동안 바로 알려준다. 실행 후 브리프 게이트에서도 다시
  // 확인하지만, 그때는 이미 만들고 난 뒤다.
  const duplicates = useMemo(() => duplicateQueryGroups(chapters), [chapters]);
  const duplicatedKeys = useMemo(() => duplicatedSectionKeys(duplicates), [duplicates]);

  // 못 채운 칸·서버가 막을 값을 타이핑하는 동안 계산한다(백엔드 검증의 거울).
  // 같은 결과를 셋이 쓴다: 칸의 빨간 테두리, 접힌 장 머리의 배지, 래퍼의 체크리스트.
  const issues = useMemo(() => collectOutlineIssues(chapters), [chapters]);
  const issueByField = useMemo(() => indexOutlineIssues(issues), [issues]);
  const issuesByChapter = useMemo(() => chapterIssueCounts(issues), [issues]);
  useEffect(() => {
    onIssuesChange?.(issues);
  }, [issues, onIssuesChange]);

  // 체크리스트에서 온 이동 - 접힌 장을 펼친 다음에야 칸이 DOM에 있다.
  // chapters를 의존성에 넣으면 타이핑마다 다시 뛰므로 요청(nonce)만 본다.
  const chaptersRef = useRef(chapters);
  chaptersRef.current = chapters;
  useEffect(() => {
    if (!focusTarget) return;
    const chapter = chaptersRef.current[focusTarget.chapterIndex];
    if (chapter) setExpandedIds((prev) => new Set(prev).add(chapter._id));
    // 절도 펼쳐야 한다 - 접힌 절은 제목 말고는 DOM에 없다(2026-08-25 절 접기 도입).
    const section =
      focusTarget.sectionIndex === null ? null : chapter?.sections[focusTarget.sectionIndex];
    if (section) setExpandedSections((prev) => new Set(prev).add(section._id));
    const timer = window.setTimeout(() => {
      const el = document.getElementById(outlineElementId(focusTarget));
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
      // 핵심 포인트·이어받기·에이전트는 입력 하나가 아니라 묶음이라 id가 감싼 div에
      // 붙는다 - 그 안의 첫 컨트롤을 잡아 줘야 "이동"이 커서까지 옮긴 게 된다.
      const focusable =
        el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement
          ? el
          : (el?.querySelector<HTMLElement>("input, textarea, button") ?? null);
      focusable?.focus({ preventScroll: true });
      onFocusHandled?.();
    }, 60);
    return () => window.clearTimeout(timer);
  }, [focusTarget, onFocusHandled]);

  return (
    <div className="flex flex-col gap-4">
      {duplicates.length > 0 ? (
        <div className="flex flex-col gap-2 rounded border border-fg-danger/40 bg-bg-danger p-3">
          <p className="text-xs font-medium text-fg-danger">
            검색 질의가 겹치는 절이 있습니다. 이 절들은 같은 자료를 인용하게 됩니다
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
                  {" · "}
                  {group.sections.map((s) => s.label).join(" · ")}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-2 rounded border border-accent/40 bg-bg-info p-3">
        <p className="text-xs text-fg-secondary">{headerInfo}</p>
        <div className="flex flex-wrap gap-1.5">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => {
              const collapse = expandedIds.size === chapters.length;
              setExpandedIds(collapse ? new Set() : new Set(chapters.map((c) => c._id)));
              // 절까지 같이 - 장만 펼치면 한 줄짜리 절 목록만 나와 "펼쳤다"는 느낌이 안 난다.
              setExpandedSections(
                collapse
                  ? new Set()
                  : new Set(chapters.flatMap((c) => c.sections.map((sec) => sec._id))),
              );
            }}
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
          issueByField={issueByField}
          issueCounts={issuesByChapter.get(ci)}
          allChapters={chapters}
          expandedSections={expandedSections}
          onToggleSection={toggleSection}
          onExpandSection={(id) => setExpandedSections((prev) => new Set(prev).add(id))}
          drag={drag}
          dropHint={dropHint}
          onDragItem={setDrag}
          onDragEnd={endDrag}
          onHint={setDropHint}
          onDropSection={dropSection}
          onDropChapter={dropChapter}
          onExpandChapter={(id) => setExpandedIds((prev) => new Set(prev).add(id))}
          onToggle={() => toggleChapter(chapter._id)}
          onChange={(next) => emit(chapters.map((c, i) => (i === ci ? next : c)))}
          onMove={(delta) => emit(move(chapters, ci, delta))}
          onRemove={() => emit(chapters.filter((_, i) => i !== ci))}
          onDuplicate={() => {
            // 같은 구성으로 여러 대상을 분석하는 용례(정책 A/B/C) - 절 구성·방향·
            // 에이전트 배정까지 통째로 복제해 바로 아래에 넣는다(2026-08-12 QA 요청).
            const dup: DraftChapter = {
              _id: draftId(),
              title: chapter.title.trim() ? `${chapter.title} (복사)` : "",
              sections: chapter.sections.map((s) => ({ ...s, _id: draftId() })),
            };
            emit([...chapters.slice(0, ci + 1), dup, ...chapters.slice(ci + 1)]);
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
          emit([...chapters, added]);
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
  issueByField,
  issueCounts,
  allChapters,
  expandedSections,
  onToggleSection,
  onExpandSection,
  drag,
  dropHint,
  onDragItem,
  onDragEnd,
  onHint,
  onDropSection,
  onDropChapter,
  onExpandChapter,
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
  /** 칸별 문제("ci:si:field") - 그 칸을 빨갛게 하고 아래에 사유를 적는다 */
  issueByField: Map<string, FormIssue>;
  /** 이 장 안의 문제 수 - 접혀 있어도 머리에 보인다 */
  issueCounts: { blockers: number; warnings: number } | undefined;
  /** 목차 전체 - 이어받기 후보를 여기서 고른다 */
  allChapters: DraftChapter[];
  /** 펼쳐진 절 _id 집합 */
  expandedSections: Set<string>;
  onToggleSection: (id: string) => void;
  onExpandSection: (id: string) => void;
  drag: DragItem | null;
  dropHint: DropHint | null;
  onDragItem: (item: DragItem | null) => void;
  onDragEnd: () => void;
  onHint: (hint: DropHint | null) => void;
  onDropSection: (hint: DropHint) => void;
  onDropChapter: (hint: DropHint) => void;
  onExpandChapter: (id: string) => void;
  onToggle: () => void;
  onChange: (next: DraftChapter) => void;
  onMove: (delta: -1 | 1) => void;
  onRemove: () => void;
  onDuplicate: () => void;
}) {
  const sectionCount = chapter.sections.filter((s) => s.title.trim()).length;
  const titleIssue = issueByField.get(`${index}:c:chapterTitle`);
  const blockers = issueCounts?.blockers ?? 0;
  const warnings = issueCounts?.warnings ?? 0;
  const dragging = drag?.chapterIndex === index && drag.sectionIndex === null;
  const chapterHint =
    drag?.sectionIndex === null &&
    dropHint?.sectionIndex === null &&
    dropHint.chapterIndex === index
      ? dropHint.position
      : null;
  const chapterHintBefore = chapterHint === "before";
  const chapterHintAfter = chapterHint === "after";
  // 절을 끌고 와 이 장에 떨어뜨리려는 상태 - 장 전체를 강조해 어디로 가는지 알린다.
  const sectionDropHere =
    drag?.sectionIndex !== null &&
    drag !== null &&
    dropHint?.chapterIndex === index &&
    dropHint.sectionIndex === null;
  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: 드롭은 포인터 전용 보강 - 같은 이동이 ↑↓ 단추(키보드 가능)로도 된다
    <section
      className={cn(
        "flex flex-col gap-3 rounded border bg-bg p-4",
        blockers > 0 ? "border-fg-danger/50" : "border-border",
        dragging && "opacity-50",
        chapterHintBefore && "border-t-2 border-t-accent",
        chapterHintAfter && "border-b-2 border-b-accent",
        sectionDropHere && "ring-2 ring-accent",
      )}
      onDragOver={(e) => {
        if (!drag) return;
        e.preventDefault();
        if (drag.sectionIndex === null) {
          onHint({
            chapterIndex: index,
            sectionIndex: null,
            position: dropPosition(e, e.currentTarget),
          });
        } else if (!expanded || chapter.sections.length === 0) {
          // 접힌 장 위에 절을 끌고 오면 펼쳐 준다 - 어디에 꽂히는지 보여야 한다.
          // 절이 하나도 없는 장은 꽂을 행이 없으므로 장 자체가 유일한 드롭 자리다.
          onExpandChapter(chapter._id);
          onHint({ chapterIndex: index, sectionIndex: null, position: "after" });
        }
      }}
      onDrop={(e) => {
        if (!drag || !dropHint) return;
        e.preventDefault();
        if (drag.sectionIndex === null) onDropChapter(dropHint);
        else onDropSection(dropHint);
      }}
    >
      <header className="flex items-center gap-2">
        {/* 손잡이만 draggable - 카드 전체를 draggable로 두면 입력칸 안 글자 선택이 막힌다.
            키보드·터치에는 옆의 ↑↓ 단추가 같은 일을 한다. */}
        <button
          type="button"
          draggable
          onDragStart={(e) => {
            e.dataTransfer.effectAllowed = "move";
            e.dataTransfer.setData("text/plain", `chapter:${index}`);
            onDragItem({ chapterIndex: index, sectionIndex: null });
          }}
          onDragEnd={onDragEnd}
          title="끌어서 장 순서 바꾸기"
          aria-label={`${index + 1}장 끌어서 옮기기`}
          className="shrink-0 cursor-grab p-0.5 text-fg-tertiary hover:text-fg active:cursor-grabbing"
        >
          <GripVertical className="h-4 w-4" aria-hidden />
        </button>
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
          id={outlineElementId({ chapterIndex: index, sectionIndex: null, field: "chapterTitle" })}
          value={chapter.title}
          maxLength={LIMITS.chapterTitle}
          placeholder="장 제목을 적으세요. 이 장의 자료를 찾을 때 검색어에 함께 들어갑니다"
          onChange={(e) => onChange({ ...chapter, title: e.target.value })}
          aria-invalid={titleIssue?.level === "blocker" ? "true" : undefined}
          className={cn("h-8", issueRing(titleIssue))}
        />
        <span className="shrink-0 text-xs text-fg-tertiary">{sectionCount}절</span>
        {/* 접힌 장이 문제를 숨기면 안 된다 - 35절 프리셋은 기본이 전부 접힘이다 */}
        {blockers > 0 ? (
          <span className="flex shrink-0 items-center gap-1 rounded-sm bg-bg-danger px-1.5 py-0.5 text-[11px] font-medium text-fg-danger">
            <AlertCircle className="h-3 w-3" aria-hidden />
            {blockers}
          </span>
        ) : null}
        {warnings > 0 ? (
          <span className="flex shrink-0 items-center gap-1 rounded-sm bg-bg-warning px-1.5 py-0.5 text-[11px] font-medium text-fg-warning">
            <TriangleAlert className="h-3 w-3" aria-hidden />
            {warnings}
          </span>
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

      {titleIssue ? (
        <div className="pl-9">
          <IssueNote issue={titleIssue} />
        </div>
      ) : null}

      {!expanded ? null : (
        <div className="flex flex-col gap-1.5 border-l-2 border-border pl-3">
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
              issueByField={issueByField}
              allChapters={allChapters}
              expanded={expandedSections.has(section._id)}
              onToggle={() => onToggleSection(section._id)}
              drag={drag}
              dropHint={dropHint}
              onDragItem={onDragItem}
              onDragEnd={onDragEnd}
              onHint={onHint}
              onDropSection={onDropSection}
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
            onClick={() => {
              const added = emptySection();
              onExpandSection(added._id); // 새 절은 적을 게 있으니 펼쳐서 넣는다
              onChange({ ...chapter, sections: [...chapter.sections, added] });
            }}
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
  issueByField,
  allChapters,
  expanded,
  onToggle,
  drag,
  dropHint,
  onDragItem,
  onDragEnd,
  onHint,
  onDropSection,
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
  /** 칸별 문제("ci:si:field") */
  issueByField: Map<string, FormIssue>;
  /** 목차 전체 - 이어받기 후보 목록의 출처 */
  allChapters: DraftChapter[];
  /** 펼쳐져 있는가 - 접히면 한 줄(번호·제목·에이전트·문제 배지)만 남는다 */
  expanded: boolean;
  onToggle: () => void;
  drag: DragItem | null;
  dropHint: DropHint | null;
  onDragItem: (item: DragItem | null) => void;
  onDragEnd: () => void;
  onHint: (hint: DropHint | null) => void;
  onDropSection: (hint: DropHint) => void;
  onChange: (next: DraftSection) => void;
  onMove: (delta: -1 | 1) => void;
  onRemove: () => void;
}) {
  const [previewing, setPreviewing] = useState(false);
  const fieldId = (field: OutlineField) =>
    outlineElementId({ chapterIndex, sectionIndex: index, field });
  const issueOf = (field: OutlineField) => issueByField.get(`${chapterIndex}:${index}:${field}`);
  const titleIssue = issueOf("sectionTitle");
  const directionIssue = issueOf("direction");
  const keyPointsIssue = issueOf("keyPoints");
  const analystIssue = issueOf("analysts");
  const buildsOnIssue = issueOf("buildsOn");
  const query = effectiveSearchQuery(chapterTitle, section.title);
  // 에이전트 질의 미리보기 - 정의는 에이전트에 있지만 "이 절에서 뭐가 검색되나"는
  // 절에서 보여야 한다(2026-08-20 결정). useAnalysts는 무한 캐시라 호출 비용 없음.
  const analystsQuery = useAnalysts();
  const agentQueries = analystQueryPreviews(
    chapterTitle,
    section.title,
    section.analysts,
    analystsQuery.data ?? [],
  );
  // 접힌 줄에도 그 절의 상태가 보여야 한다 - 35절을 접어 두고도 어디가 비었는지 안다.
  const rowIssues = (["sectionTitle", "direction", "keyPoints", "analysts", "buildsOn"] as const)
    .map((f) => issueOf(f))
    .filter((i): i is FormIssue => i !== undefined);
  const rowBlockers = rowIssues.filter((i) => i.level === "blocker").length;
  const rowWarnings = rowIssues.length - rowBlockers;
  const dragging = drag?.chapterIndex === chapterIndex && drag.sectionIndex === index;
  const hint =
    drag?.sectionIndex !== null &&
    drag !== null &&
    dropHint?.chapterIndex === chapterIndex &&
    dropHint.sectionIndex === index
      ? dropHint.position
      : null;

  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: 드롭은 포인터 전용 보강 - 같은 이동이 ↑↓ 단추(키보드 가능)로도 된다
    <div
      className={cn(
        "flex flex-col gap-2 rounded border bg-bg-secondary",
        // 접힌 줄은 촘촘하게 - 35절을 훑는 화면이라 줄 높이가 그대로 스크롤이 된다.
        expanded ? "p-3" : "p-2",
        titleIssue?.level === "blocker" ? "border-fg-danger/50" : "border-border",
        dragging && "opacity-50",
        hint === "before" && "border-t-2 border-t-accent",
        hint === "after" && "border-b-2 border-b-accent",
      )}
      onDragOver={(e) => {
        if (!drag || drag.sectionIndex === null) return;
        e.preventDefault();
        e.stopPropagation();
        onHint({
          chapterIndex,
          sectionIndex: index,
          position: dropPosition(e, e.currentTarget),
        });
      }}
      onDrop={(e) => {
        if (!drag || drag.sectionIndex === null || !dropHint) return;
        e.preventDefault();
        e.stopPropagation();
        onDropSection(dropHint);
      }}
    >
      <div className="flex items-center gap-2">
        <button
          type="button"
          draggable
          onDragStart={(e) => {
            e.dataTransfer.effectAllowed = "move";
            e.dataTransfer.setData("text/plain", `section:${chapterIndex}.${index}`);
            onDragItem({ chapterIndex, sectionIndex: index });
          }}
          onDragEnd={onDragEnd}
          title="끌어서 절 순서 바꾸기 (다른 장으로도 옮길 수 있습니다)"
          aria-label={`${chapterIndex + 1}.${index + 1}절 끌어서 옮기기`}
          className="shrink-0 cursor-grab p-0.5 text-fg-tertiary hover:text-fg active:cursor-grabbing"
        >
          <GripVertical className="h-4 w-4" aria-hidden />
        </button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 w-7 shrink-0 p-0"
          onClick={onToggle}
          aria-expanded={expanded}
          aria-label={`${chapterIndex + 1}.${index + 1}절 ${expanded ? "접기" : "펼치기"}`}
        >
          {expanded ? (
            <ChevronDown className="h-4 w-4" aria-hidden />
          ) : (
            <ChevronRight className="h-4 w-4" aria-hidden />
          )}
        </Button>
        <span className="shrink-0 font-mono text-xs text-fg-tertiary">
          {chapterIndex + 1}.{index + 1}
        </span>
        <Input
          id={fieldId("sectionTitle")}
          value={section.title}
          maxLength={LIMITS.sectionTitle}
          placeholder="절 제목을 적으세요. 목차와 본문 헤딩에 그대로 실립니다"
          onChange={(e) => onChange({ ...section, title: e.target.value })}
          aria-invalid={titleIssue?.level === "blocker" ? "true" : undefined}
          className={cn("h-8 bg-bg", issueRing(titleIssue))}
        />
        {/* 접힌 줄 요약 - 누가 쓰는지, 무엇이 비었는지 */}
        {!expanded && section.analysts.length > 0 ? (
          <span className="hidden shrink-0 truncate text-xs text-fg-tertiary sm:inline">
            {section.analysts[0]}
            {section.analysts.length > 1 ? ` +${section.analysts.length - 1}` : ""}
          </span>
        ) : null}
        {!expanded && rowBlockers > 0 ? (
          <span className="flex shrink-0 items-center gap-1 rounded-sm bg-bg-danger px-1.5 py-0.5 text-[11px] font-medium text-fg-danger">
            <AlertCircle className="h-3 w-3" aria-hidden />
            {rowBlockers}
          </span>
        ) : null}
        {!expanded && rowWarnings > 0 ? (
          <span className="flex shrink-0 items-center gap-1 rounded-sm bg-bg-warning px-1.5 py-0.5 text-[11px] font-medium text-fg-warning">
            <TriangleAlert className="h-3 w-3" aria-hidden />
            {rowWarnings}
          </span>
        ) : null}
        <RowControls
          canUp={index > 0}
          canDown={index < count - 1}
          onMove={onMove}
          onRemove={onRemove}
          label={`${chapterIndex + 1}.${index + 1}절`}
        />
      </div>
      {titleIssue ? (
        <div className="pl-8">
          <IssueNote issue={titleIssue} />
        </div>
      ) : null}
      {/* 접으면 한 줄만 남는다 - 35절 목차에서 구조를 보려면 세부는 접혀 있어야 한다.
          펼친 절만 세부를 그린다(접힌 절의 칸은 DOM에 없으므로, 체크리스트의 '이동'은
          그 절을 먼저 펼친다). */}
      {expanded ? (
        <>
          <Field
            hint="작성 방향(권장). 이 절이 무엇을 논증해야 하는지 한 줄로 적습니다"
            helpTitle="작성 방향은 무엇인가요?"
            help={
              <>
                <p>
                  이 절이 <b>무엇을 밝혀야 하는 글인지</b> 한 줄로 적는 칸입니다. 적은 문장이 본문을
                  쓰는 지시문에 그대로 들어갑니다.
                </p>
                <p>
                  비워도 만들어지지만 그러면 절 제목만 보고 씁니다. 같은 제목이라도 "현황을
                  나열"할지 "타당성을 논증"할지가 갈리므로 한 줄 적는 편이 낫습니다.
                </p>
                <p>예: "관련 법령과 상위 계획에 비춘 국고 지원의 당위성 논증"</p>
              </>
            }
            issue={directionIssue}
          >
            <Input
              id={fieldId("direction")}
              value={section.direction}
              placeholder="예: 관련 법령과 상위 계획에 비춘 국고 지원의 당위성 논증"
              onChange={(e) => onChange({ ...section, direction: e.target.value })}
              className={cn("h-8 bg-bg text-sm", issueRing(directionIssue))}
            />
          </Field>
          <Field
            hint="핵심 포인트(필수). 이 절에서 반드시 다룰 항목입니다"
            helpTitle="핵심 포인트는 무엇인가요?"
            help={
              <>
                <p>
                  이 절에 <b>반드시 들어가야 할 항목</b>을 한 줄에 하나씩 적습니다. 두 가지 일을
                  합니다.
                </p>
                <p>
                  첫째, <b>항목마다 자료 검색이 한 번씩</b> 돕니다. 항목이 곧 검색어가 되므로
                  구체적일수록 좋은 자료가 붙습니다.
                </p>
                <p>
                  둘째, 다 썼는지 확인하는 <b>체크리스트</b>가 됩니다.
                </p>
                <p>예: "상위 계획 연계", "추진 경위", "총사업비 규모"</p>
              </>
            }
            issue={keyPointsIssue}
          >
            <div id={fieldId("keyPoints")}>
              <KeyPointsEditor
                points={section.key_points}
                invalid={keyPointsIssue?.level === "blocker"}
                onChange={(key_points) => onChange({ ...section, key_points })}
              />
            </div>
          </Field>
          <div id={fieldId("analysts")}>
            <p className="mb-1 flex items-center gap-1 text-[11px] text-fg-tertiary">
              <span>담당 에이전트(권장). 이 절을 쓰는 관점과 목표 분량을 정합니다</span>
              <HelpTip title="담당 에이전트란?">
                <p>
                  사람이 아니라 <b>글을 쓰는 관점</b>입니다. "시장분석"을 고르면 시장 분석가의
                  시각과 분량 기준으로, "정책동향"을 고르면 정책 담당자의 시각으로 그 절을 씁니다.
                </p>
                <p>
                  고른 에이전트는 <b>검색어도 함께 정합니다</b>. 관점마다 찾는 자료가 다릅니다.
                </p>
                <p>
                  안 고르면 기본 규칙만 적용되고 <b>목표 분량이 없어 절이 짧아집니다</b>. 중요한
                  절은 2~3개를 골라 여러 관점을 함께 반영하세요.
                </p>
              </HelpTip>
            </p>
            <AnalystPicker
              selected={section.analysts}
              onChange={(analysts) => onChange({ ...section, analysts })}
            />
            <IssueNote issue={analystIssue} />
          </div>
          <Field
            hint="앞 절 이어받기(선택). 앞 절이 확정한 수치를 그대로 받아 씁니다"
            helpTitle="이어받기는 언제 쓰나요?"
            help={
              <>
                <p>
                  뒤 절이 앞 절의 <b>숫자를 다시 말해야 할 때</b> 씁니다. 4.1에서 총사업비를
                  산정하고 6.2에서 그 금액으로 재원을 배분한다면, 6.2가 4.1을 이어받습니다.
                </p>
                <p>
                  이어받지 않으면 뒤 절은 그 숫자를 <b>다른 자료에서 다시 찾거나 지어냅니다</b>.
                  같은 보고서 안에서 총사업비가 두 개가 되는 일이 실제로 있었습니다.
                </p>
                <p>
                  특정 값만 받으려면 고른 절 옆 '지표' 칸에 이름을 적으세요(예: 총사업비). 장 전체를
                  고르면 그 장에서 확정된 값을 모두 받으므로, 장 끝의 시사점 절에 적합합니다.
                </p>
                <p>순서를 바꾸는 기능은 아닙니다. 순서는 목차에서 끌어 옮기면 됩니다.</p>
              </>
            }
            issue={buildsOnIssue}
          >
            <div id={fieldId("buildsOn")}>
              <BuildsOnPicker
                value={section.builds_on}
                onChange={(builds_on) => onChange({ ...section, builds_on })}
                chapters={allChapters}
                selfChapter={chapterIndex}
                selfSection={index}
                issue={buildsOnIssue}
              />
            </div>
          </Field>
          {/* 이 절이 실제로 무엇으로 검색되는지 - 장 제목이 함께 들어간다는 사실을
            설명문으로 말하는 대신 결과를 보여준다. 확정 값은 실행 후 설계 검토 화면이
            서버에서 계산해 다시 보여준다. */}
          {query ? (
            <p className="pl-8 text-[11px] text-fg-tertiary">
              검색:{" "}
              <code
                className={cn("font-mono", duplicated ? "text-fg-danger" : "text-fg-secondary")}
              >
                {query}
              </code>
              {duplicated ? <span className="text-fg-danger"> (다른 절과 겹칩니다)</span> : null}
            </p>
          ) : null}
          {agentQueries.length > 0 ? (
            <p className="pl-8 text-[11px] text-fg-tertiary">
              에이전트 검색:{" "}
              <code className="font-mono text-fg-secondary">{agentQueries.join(" · ")}</code>
            </p>
          ) : null}
          <button
            type="button"
            className="self-start text-[11px] text-fg-tertiary underline"
            onClick={() => setPreviewing(true)}
          >
            이 절의 최종 프롬프트 미리보기
          </button>
        </>
      ) : null}
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
/** 핵심 포인트 편집기 - 항목마다 입력칸 하나. "줄마다 하나"라는 텍스트 규약을 설명
 * 없이 UI가 그대로 드러낸다(2026-08-20 사용자 제안). 데이터는 원래 string[]라 뷰만
 * 바뀐다. Enter=아래에 새 항목, 여러 줄 붙여넣기=항목 자동 분리(일괄 입력 유지),
 * 빈 항목에서 Backspace=그 항목 제거. */
function KeyPointsEditor({
  points,
  invalid,
  onChange,
}: {
  points: string[];
  /** 필수인데 채워진 항목이 하나도 없다 - 적을 자리를 빨갛게 짚어 준다 */
  invalid?: boolean;
  onChange: (points: string[]) => void;
}) {
  // 항목 키는 순번이 아니라 로컬 id - 중간 삽입·삭제 시 뒤 항목 입력칸이 통째로
  // 갈아끼워지지 않게 한다. 외부 리셋(프리셋 골격 교체)은 길이 재조정으로 흡수.
  const idsRef = useRef<string[]>([]);
  if (idsRef.current.length !== points.length) {
    const ids = idsRef.current.slice(0, points.length);
    while (ids.length < points.length) ids.push(draftId());
    idsRef.current = ids;
  }
  const rowRefs = useRef<(HTMLInputElement | null)[]>([]);
  const focusIdx = useRef<number | null>(null);
  useEffect(() => {
    if (focusIdx.current !== null) {
      rowRefs.current[focusIdx.current]?.focus();
      focusIdx.current = null;
    }
  });

  const setAt = (i: number, value: string) => {
    if (value.includes("\n")) {
      // 여러 줄 붙여넣기 - 줄마다 항목으로 분리(종전 textarea 워크플로 보존)
      const lines = value.split("\n");
      idsRef.current.splice(i, 1, ...lines.map(() => draftId()));
      focusIdx.current = i + lines.length - 1;
      onChange([...points.slice(0, i), ...lines, ...points.slice(i + 1)]);
      return;
    }
    onChange(points.map((p, idx) => (idx === i ? value : p)));
  };
  const insertAfter = (i: number) => {
    idsRef.current.splice(i + 1, 0, draftId());
    focusIdx.current = i + 1;
    onChange([...points.slice(0, i + 1), "", ...points.slice(i + 1)]);
  };
  const removeAt = (i: number) => {
    idsRef.current.splice(i, 1);
    focusIdx.current = i > 0 ? i - 1 : null;
    onChange(points.filter((_, idx) => idx !== i));
  };

  return (
    <div className="flex flex-col gap-1.5">
      {points.map((point, i) => (
        <div key={idsRef.current[i]} className="flex items-center gap-1.5">
          <Input
            ref={(el) => {
              rowRefs.current[i] = el;
            }}
            value={point}
            placeholder={i === 0 ? "예: 관련 법률" : undefined}
            onChange={(e) => setAt(i, e.target.value)}
            onPaste={(e) => {
              // input은 붙여넣기 줄바꿈을 브라우저가 지워버린다 - 클립보드를 직접
              // 읽어 항목으로 분리한다(빈 항목이면 대체, 아니면 아래에 삽입).
              const text = e.clipboardData.getData("text");
              if (!text.includes("\n")) return;
              e.preventDefault();
              const lines = text
                .split("\n")
                .map((l) => l.trim())
                .filter(Boolean);
              if (lines.length === 0) return;
              if (point.trim() === "") {
                setAt(i, lines.join("\n"));
              } else {
                idsRef.current.splice(i + 1, 0, ...lines.map(() => draftId()));
                focusIdx.current = i + lines.length;
                onChange([...points.slice(0, i + 1), ...lines, ...points.slice(i + 1)]);
              }
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                insertAfter(i);
              } else if (e.key === "Backspace" && point === "") {
                e.preventDefault();
                removeAt(i);
              }
            }}
            className={cn("h-8 bg-bg text-sm", invalid && "border-fg-danger")}
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label="핵심 포인트 삭제"
            onClick={() => removeAt(i)}
            className="h-8 w-8 shrink-0 p-0 text-fg-tertiary hover:text-fg-danger"
          >
            <X className="h-3.5 w-3.5" aria-hidden />
          </Button>
        </div>
      ))}
      <Button
        type="button"
        variant="outline"
        size="sm"
        className={cn("w-fit", invalid && "border-fg-danger text-fg-danger")}
        onClick={() => {
          focusIdx.current = points.length;
          onChange([...points, ""]);
        }}
      >
        <Plus className="mr-1 h-3.5 w-3.5" aria-hidden />
        핵심 포인트 추가
      </Button>
    </div>
  );
}

function Field({
  hint,
  help,
  helpTitle,
  issue,
  children,
}: {
  hint: string;
  /** 자세한 설명 - 물음표를 눌렀을 때만 뜬다(화면에는 한 줄만 남긴다) */
  help?: React.ReactNode;
  helpTitle?: string;
  /** 이 칸의 문제 - 있으면 입력칸 아래에 사유 한 줄 */
  issue?: FormIssue;
  children: React.ReactNode;
}) {
  // label이 아니라 div - 자식이 Input/Textarea 중 무엇이든 오고, 컨트롤 id를
  // 여기서 알 수 없다(연결 없는 label은 스크린리더에 더 나쁘다).
  return (
    <div className="flex flex-col gap-1">
      <p className="flex items-center gap-1 text-[11px] text-fg-tertiary">
        <span>{hint}</span>
        {help ? <HelpTip title={helpTitle ?? hint}>{help}</HelpTip> : null}
      </p>
      {children}
      <IssueNote issue={issue} />
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

/** 에이전트 목록을 고르기 좋게 묶는다: 시스템 → 내 것 → 공개한 **사람별**.
 *
 * 공유를 한 뭉치로 두면 여러 명이 공개했을 때 누구 것을 고르는지 목록에서 안 보인다
 * (2026-08-25 요청). 사람 이름은 가나다순 - 목록 순서가 남의 수정 시각에 따라
 * 흔들리지 않게 한다. */
function analystGroups(analysts: Analyst[]): { label: string; items: Analyst[] }[] {
  const groups = [
    { label: "시스템", items: analysts.filter((a) => !a.shared && !a.id.startsWith("u-")) },
    { label: "내 에이전트", items: analysts.filter((a) => !a.shared && a.id.startsWith("u-")) },
  ];
  const byOwner = new Map<string, Analyst[]>();
  for (const a of analysts) {
    if (!a.shared) continue;
    const owner = a.owner_name?.trim() || "다른 사용자";
    const bucket = byOwner.get(owner);
    if (bucket) bucket.push(a);
    else byOwner.set(owner, [a]);
  }
  for (const owner of [...byOwner.keys()].sort((x, y) => x.localeCompare(y, "ko"))) {
    groups.push({ label: `${owner}의 에이전트`, items: byOwner.get(owner) ?? [] });
  }
  return groups;
}

/** 칩에 쓸 이름 - 사람별 그룹 머리가 이미 소유자를 말하므로 이름 끝의 "(소유자)"는 뗀다.
 * 카탈로그의 진짜 이름(목차에 저장되는 값)은 그대로다 - 표시만 짧게 한다. */
function chipLabel(a: Analyst): string {
  const suffix = a.owner_name ? ` (${a.owner_name})` : "";
  return suffix && a.name.endsWith(suffix) ? a.name.slice(0, -suffix.length) : a.name;
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
        <span className="text-fg-tertiary">고르기</span>{" "}
        {/* 설명은 위 도움말로 옮겼다 - 접힌 줄에는 "무엇을 골랐는지"만 남긴다 */}
        {selected.length > 0 ? (
          <span className="font-medium text-fg">
            {selected.join(" · ")}
            {selected.length > 1 ? (
              <span className="ml-1 font-normal text-fg-tertiary">
                {selected.length}개 관점을 모두 반영
              </span>
            ) : null}
          </span>
        ) : (
          <span className="text-fg-tertiary">아직 고르지 않았습니다</span>
        )}
      </summary>
      <div className="flex flex-wrap gap-1.5 border-t border-border p-3">
        {analystsQuery.isLoading ? (
          <p className="text-xs text-fg-tertiary">카탈로그 불러오는 중…</p>
        ) : (
          // 출처별 구분(사용자 요청 2026-08-20): 이름만 보면 시스템/내 것/남의 공유가
          // 안 갈린다. 개인·공유는 id가 "u-"로 시작하고 공유는 shared 플래그가 선다.
          // 공유는 한 뭉치가 아니라 **사람별로** 나눈다(2026-08-25 요청) - 여러 명이
          // 공개하면 누구 것을 고르는지가 목록에서 바로 보여야 한다.
          analystGroups(analysts)
            .filter((g) => g.items.length > 0)
            .map((g) => (
              <div key={g.label} className="flex w-full flex-wrap items-center gap-1.5">
                <span className="w-full text-[11px] font-medium text-fg-tertiary">{g.label}</span>
                {g.items.map((a) => {
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
                      {chipLabel(a)}
                      <span className="ml-1 text-[10px] text-fg-tertiary">{a.cat}</span>
                    </button>
                  );
                })}
              </div>
            ))
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
