/** 생성 폼의 입력 계약 - 백엔드 검증(api/routers/projects._validate_outline_config,
 * core/outline.normalize_outline, api/schemas/project)의 거울.
 *
 * 왜 거울을 두는가: 지금까지 목차 쪽 계약은 서버만 알고 있었다. 절이 40개를 넘어도,
 * 이어받기가 없는 절을 가리켜도, 제목 없는 절이 조용히 버려져도 폼은 아무 말이
 * 없다가 "프로젝트 생성" 한 번에 422 토스트 한 줄로 끝났다 - 어느 절이 문제인지도
 * 알려주지 않았다(2026-08-25 사용자 지적).
 *
 * 여기는 순수 계산만 한다(DOM·폼 라이브러리 무관). 화면 둘이 같은 결과를 쓴다:
 *   - ReadinessPanel: 사이드바 체크리스트("무엇이 빠져서 생성이 안 되나")
 *   - OutlineEditor:  그 칸에 빨간 테두리 + 그 자리에 사유 한 줄
 *
 * 어긋나면 서버가 진실이다 - 상한을 고칠 때는 아래 주석의 출처를 같이 본다.
 */

import { duplicateQueryGroups } from "./searchPreview";

export const LIMITS = {
  /** ProjectBase.title: min_length=1, max_length=255 */
  title: 255,
  /** 서버 상한 없음 - 주제는 수집 질의의 틀이라 길수록 흐려진다(폼 상한, schema.ts와 같은 값) */
  topic: 2000,
  /** OutlineChapterIn.title: max_length=255 (빈 문자열 허용) */
  chapterTitle: 255,
  /** OutlineSectionIn.title: min_length=1, max_length=255 */
  sectionTitle: 255,
  /** services/generation/planner.MAX_SECTIONS - 넘으면 생성이 422로 막힌다 */
  sections: 40,
  /** core/builds_on.MAX_REFS_PER_SECTION */
  refsPerSection: 2,
  /** projects._validate_rules_config */
  rules: 10,
} as const;

/** 목차 편집기 안의 칸 종류 - 이동·빨간 표시의 좌표. */
export type OutlineField =
  | "chapterTitle"
  | "sectionTitle"
  | "direction"
  | "keyPoints"
  | "analysts"
  | "buildsOn";

export type IssueTarget =
  | { kind: "field"; elementId: string }
  | {
      kind: "outline";
      chapterIndex: number;
      /** null = 장 자체(장 제목) */
      sectionIndex: number | null;
      field: OutlineField;
    };

export type IssueLevel = "blocker" | "warning";

export interface FormIssue {
  /** 안정 키(리스트 렌더용) */
  id: string;
  /** 사유 종류 - 체크리스트가 같은 종류를 한 줄로 접을 때 쓴다(칸 표시는 접지 않는다) */
  kind: string;
  /** blocker = 이게 있으면 생성이 막힌다, warning = 만들어지지만 결과가 나빠진다 */
  level: IssueLevel;
  /** 어디인가 - "보고서 제목" | "3.2절" */
  where: string;
  /** 무엇이 빠졌는가 - 한 줄, 고치는 법까지 */
  message: string;
  target: IssueTarget;
  /** 같은 사유가 여러 곳이면 나머지 개수(이동은 첫 곳으로) */
  more?: number;
  /** 그 칸을 빨갛게 물들일지. false면 체크리스트에만 뜬다(이동은 그대로 된다).
   *
   * 목차 전체를 두고 하는 말(절이 없다·너무 많다·번호가 밀린다)은 특정 칸의 잘못이
   * 아니다. 그런데도 아무 칸에 칠하면, 자유 주제로 막 시작한 사람이 **한 글자도 치기
   * 전에** 빨간 칸과 배지부터 본다(2026-08-25 전수 조사). 사유는 체크리스트가 말한다. */
  paint?: boolean;
}

interface SectionLike {
  title: string;
  direction: string;
  key_points: string[];
  analysts: string[];
  builds_on: string[];
}

interface ChapterLike {
  title: string;
  sections: SectionLike[];
}

/** builds_on 사람 표기 - core/builds_on._REF_RE의 거울("4.1" | "4.1(총사업비)" | "4.*"). */
const REF_RE = /^\s*(\d+)\.(\d+|\*)(?:\(\s*([^()]+?)\s*\))?\s*$/;

/** 이어받기 표기 1개의 해석 - section=null이면 장 전체("4.*"). */
export interface ParsedRef {
  chapter: number;
  section: number | null;
  metric: string | null;
}

/** 표기 → 해석. 못 읽으면 null(고르는 UI에서는 옛 값·손편집만 여기 걸린다). */
export function parseRefLabel(raw: string): ParsedRef | null {
  const m = REF_RE.exec(raw);
  if (!m) return null;
  const section = m[2] === "*" ? null : Number(m[2]);
  // 장 전체 참조에 지표 지정은 뜻이 없다 - core/builds_on.parse_ref와 같은 규칙.
  return { chapter: Number(m[1]), section, metric: section === null ? null : (m[3] ?? null) };
}

/** 해석 → 표기. 저장되는 문자열은 반드시 이 함수를 거친다(손으로 조립하지 않는다). */
export function formatRefLabel(ref: ParsedRef): string {
  const base = `${ref.chapter}.${ref.section === null ? "*" : ref.section}`;
  return ref.section !== null && ref.metric?.trim() ? `${base}(${ref.metric.trim()})` : base;
}

const outlineTarget = (
  chapterIndex: number,
  sectionIndex: number | null,
  field: OutlineField,
): IssueTarget => ({ kind: "outline", chapterIndex, sectionIndex, field });

/** 제출되는 절만 센다 - toOutline이 제목 없는 절과 빈 장을 버린다. */
export function countSubmittedSections(chapters: ChapterLike[]): number {
  return chapters.reduce((n, ch) => n + ch.sections.filter((s) => s.title.trim()).length, 0);
}

/** 같은 종류가 여러 곳이면 체크리스트에서 한 줄로 접는다 - 35절 프리셋에서는
 * "작성 방향 없음"만 30줄이 되어 정작 막는 사유가 묻힌다. 접는 것은 목록뿐이고,
 * 편집기의 칸 표시(빨간 테두리)는 전부 그대로 남는다. */
export function foldIssues(issues: FormIssue[]): FormIssue[] {
  const out: FormIssue[] = [];
  const seen = new Map<string, number>(); // kind -> out의 위치
  for (const issue of issues) {
    const at = seen.get(issue.kind);
    if (at === undefined) {
      seen.set(issue.kind, out.length);
      out.push(issue);
    } else {
      out[at] = { ...out[at], more: (out[at].more ?? 0) + 1 };
    }
  }
  return out;
}

/** 목차만 본 문제 목록. 프리셋 편집기처럼 제목·주제가 없는 화면도 이것만 쓴다. */
export function collectOutlineIssues(chapters: ChapterLike[]): FormIssue[] {
  const issues: FormIssue[] = [];

  // 제출 후의 번호를 미리 계산한다. 제목 없는 절·빈 장은 저장할 때 사라지므로,
  // 편집기에 보이는 번호와 서버가 이어받기를 해석하는 번호가 어긋날 수 있다.
  const survivingLabels = new Set<string>(); // 살아남는 절 - 편집기 번호 기준
  const survivingChapters = new Set<number>(); // 살아남는 장 - 편집기 번호(1-base)
  let shifted = false;
  let cleanCi = 0;
  chapters.forEach((ch, ci) => {
    const titled = ch.sections.filter((s) => s.title.trim());
    if (titled.length === 0) return;
    cleanCi += 1;
    survivingChapters.add(ci + 1);
    if (cleanCi !== ci + 1) shifted = true;
    let cleanSi = 0;
    ch.sections.forEach((s, si) => {
      if (!s.title.trim()) return;
      cleanSi += 1;
      survivingLabels.add(`${ci + 1}.${si + 1}`);
      if (cleanSi !== si + 1) shifted = true;
    });
  });

  const total = countSubmittedSections(chapters);
  if (total === 0) {
    issues.push({
      id: "outline-empty",
      kind: "outline-empty",
      level: "blocker",
      paint: false,
      where: "목차",
      message: "제목이 있는 절이 하나도 없습니다. 장·절을 1개 이상 만들어야 시작합니다.",
      target: outlineTarget(0, 0, "sectionTitle"),
    });
  } else if (total > LIMITS.sections) {
    issues.push({
      id: "outline-too-large",
      kind: "outline-too-large",
      level: "blocker",
      paint: false,
      where: "목차",
      message: `절이 ${total}개입니다. 최대 ${LIMITS.sections}개까지만 만들 수 있습니다.`,
      target: outlineTarget(Math.max(chapters.length - 1, 0), null, "chapterTitle"),
    });
  }

  const emptyChapterTitles: FormIssue[] = [];
  const droppedChapters: FormIssue[] = [];
  const untitledSections: FormIssue[] = [];
  const noDirection: FormIssue[] = [];
  const noAnalyst: FormIssue[] = [];
  const blankPoints: FormIssue[] = [];

  chapters.forEach((ch, ci) => {
    const chLabel = `${ci + 1}장`;
    const titledCount = ch.sections.filter((s) => s.title.trim()).length;

    if (ch.title.trim().length > LIMITS.chapterTitle) {
      issues.push({
        id: `ch-${ci}-title-long`,
        kind: "ch-title-long",
        level: "blocker",
        where: chLabel,
        message: `장 제목이 ${LIMITS.chapterTitle}자를 넘습니다.`,
        target: outlineTarget(ci, null, "chapterTitle"),
      });
    } else if (!ch.title.trim() && titledCount > 0) {
      emptyChapterTitles.push({
        id: `ch-${ci}-title-empty`,
        kind: "ch-title-empty",
        level: "warning",
        where: chLabel,
        message: "장 제목이 비어 있어 절 제목만으로 자료를 찾게 됩니다.",
        target: outlineTarget(ci, null, "chapterTitle"),
      });
    }

    if (titledCount === 0 && chapters.length > 1) {
      droppedChapters.push({
        id: `ch-${ci}-dropped`,
        kind: "ch-dropped",
        level: "warning",
        where: chLabel,
        message: "제목이 있는 절이 없어 이 장은 저장되지 않습니다.",
        target: outlineTarget(ci, 0, "sectionTitle"),
      });
    }

    ch.sections.forEach((s, si) => {
      const label = `${ci + 1}.${si + 1}절`;
      const title = s.title.trim();
      const hasContent =
        s.direction.trim().length > 0 ||
        s.key_points.some((k) => k.trim()) ||
        s.analysts.length > 0 ||
        s.builds_on.length > 0;

      if (!title) {
        // 빈 줄 하나는 편집 중 자연스러운 상태다. 내용까지 적어 놓고 제목만 빠진
        // 절은 통째로 사라지므로 따로, 더 세게 알린다.
        // 제목 있는 절이 아직 하나도 없으면(=막 시작한 목차) 위의 '절이 없습니다'가
        // 이미 같은 말을 한다 - 빈 줄마다 또 경고하면 시작하자마자 잔소리가 된다.
        if (!hasContent && total === 0) return;
        untitledSections.push({
          id: `sec-${ci}-${si}-untitled`,
          kind: hasContent ? "sec-untitled-content" : "sec-untitled",
          level: hasContent ? "blocker" : "warning",
          where: label,
          message: hasContent
            ? "내용은 적었는데 절 제목이 없습니다. 이대로 만들면 이 절이 통째로 사라집니다."
            : "절 제목이 비어 있어 저장할 때 이 줄은 사라집니다.",
          target: outlineTarget(ci, si, "sectionTitle"),
        });
        return;
      }

      if (title.length > LIMITS.sectionTitle) {
        issues.push({
          id: `sec-${ci}-${si}-title-long`,
          kind: "sec-title-long",
          level: "blocker",
          where: label,
          message: `절 제목이 ${LIMITS.sectionTitle}자를 넘습니다.`,
          target: outlineTarget(ci, si, "sectionTitle"),
        });
      }

      // 핵심 포인트는 **필수**(2026-08-25 사용자 결정). 서버는 빈 값을 받지만,
      // 이 항목이 없으면 그 절은 검색 어휘도 작성 체크리스트도 없이 제목 한 줄로
      // 쓰이게 된다 - 시스템 프리셋 8종 146절이 전부 채워져 있는 것도 그래서다
      // (실측: 빈 곳 0). 따라서 사람이 절을 새로 추가했을 때만 이 사유가 뜬다.
      const filledPoints = s.key_points.filter((k) => k.trim());
      if (filledPoints.length === 0) {
        issues.push({
          id: `sec-${ci}-${si}-no-key-points`,
          kind: "sec-no-key-points",
          level: "blocker",
          where: label,
          message:
            "핵심 포인트가 없습니다. 반드시 다룰 항목을 1개 이상 적으세요(항목마다 자료 검색이 한 번씩 돕니다).",
          target: outlineTarget(ci, si, "keyPoints"),
        });
      } else if (filledPoints.length < s.key_points.length) {
        // 빈 줄은 저장할 때 버려진다(toOutline) - 적어 놓은 줄이 사라지는 것처럼
        // 보이지 않게 미리 알린다. 항목이 하나도 없을 때는 위에서 이미 막았다.
        blankPoints.push({
          id: `sec-${ci}-${si}-blank-key-points`,
          kind: "sec-blank-key-points",
          level: "warning",
          where: label,
          message: `핵심 포인트에 빈 줄이 ${s.key_points.length - filledPoints.length}개 있습니다. 저장할 때 사라집니다.`,
          target: outlineTarget(ci, si, "keyPoints"),
        });
      }

      if (!s.direction.trim()) {
        noDirection.push({
          id: `sec-${ci}-${si}-no-direction`,
          kind: "sec-no-direction",
          level: "warning",
          where: label,
          message: "작성 방향이 비어 있어 절 제목만 보고 쓰게 됩니다.",
          target: outlineTarget(ci, si, "direction"),
        });
      }
      if (s.analysts.length === 0) {
        noAnalyst.push({
          id: `sec-${ci}-${si}-no-analyst`,
          kind: "sec-no-analyst",
          level: "warning",
          where: label,
          message: "담당 에이전트가 없어 관점·분량 목표 없이 기본 규칙만 적용됩니다.",
          target: outlineTarget(ci, si, "analysts"),
        });
      }

      // 앞 절 이어받기 - 서버(normalize_outline)가 422로 막는 것과 같은 검사를
      // 같은 순서로 한다. 여기서 잡아야 만들기 전에 고칠 수 있다.
      const refs = s.builds_on.map((r) => r.trim()).filter(Boolean);
      const accepted = new Set<string>();
      for (const raw of refs) {
        const m = REF_RE.exec(raw);
        if (!m) {
          issues.push({
            id: `sec-${ci}-${si}-ref-bad-${raw}`,
            kind: "sec-ref-bad",
            level: "blocker",
            where: label,
            message: `이어받기 표기를 읽을 수 없습니다: ${raw}. 절 번호로 적으세요(예: 4.1, 4.1(총사업비), 4.*).`,
            target: outlineTarget(ci, si, "buildsOn"),
          });
          continue;
        }
        const chapterNo = Number(m[1]);
        const isWhole = m[2] === "*";
        const refLabel = `${chapterNo}.${m[2]}`;
        if (isWhole) {
          if (!survivingChapters.has(chapterNo)) {
            issues.push({
              id: `sec-${ci}-${si}-ref-noch-${raw}`,
              kind: "sec-ref-noch",
              level: "blocker",
              where: label,
              message: `이어받기가 없는 장을 가리킵니다: ${refLabel}`,
              target: outlineTarget(ci, si, "buildsOn"),
            });
            continue;
          }
        } else if (refLabel === `${ci + 1}.${si + 1}`) {
          issues.push({
            id: `sec-${ci}-${si}-ref-self`,
            kind: "sec-ref-self",
            level: "blocker",
            where: label,
            message: "이어받기가 자기 자신을 가리킵니다.",
            target: outlineTarget(ci, si, "buildsOn"),
          });
          continue;
        } else if (!survivingLabels.has(refLabel)) {
          issues.push({
            id: `sec-${ci}-${si}-ref-nosec-${raw}`,
            kind: "sec-ref-nosec",
            level: "blocker",
            where: label,
            message: `이어받기가 없는 절을 가리킵니다: ${refLabel}`,
            target: outlineTarget(ci, si, "buildsOn"),
          });
          continue;
        }
        const key = isWhole ? `c${chapterNo}` : refLabel;
        if (accepted.has(key)) continue;
        accepted.add(key);
        if (accepted.size > LIMITS.refsPerSection) {
          issues.push({
            id: `sec-${ci}-${si}-ref-many`,
            kind: "sec-ref-many",
            level: "blocker",
            where: label,
            message: `이어받기는 절당 ${LIMITS.refsPerSection}개까지입니다. 더 필요하면 장 전체(${ci + 1}.*)로 적으세요.`,
            target: outlineTarget(ci, si, "buildsOn"),
          });
          break;
        }
      }
    });
  });

  // 번호 밀림 - 사라지는 줄 때문에 이어받기가 말없이 다른 절을 가리키게 된다.
  // 서버는 제출된 목차의 위치로만 해석하므로, 여기서 막지 않으면 아무도 모른다.
  const usesRefs = chapters.some((ch) => ch.sections.some((s) => s.builds_on.length > 0));
  if (shifted && usesRefs) {
    issues.push({
      id: "outline-renumber",
      kind: "outline-renumber",
      level: "blocker",
      paint: false,
      where: "목차",
      message:
        "저장할 때 사라지는 줄(제목 없는 절·빈 장) 때문에 절 번호가 당겨집니다. 이어받기가 다른 절을 가리키게 되니 제목을 채우거나 그 줄을 지우세요.",
      target: outlineTarget(0, 0, "sectionTitle"),
    });
  }

  for (const group of duplicateQueryGroups(chapters)) {
    const first = group.sections[0];
    issues.push({
      id: `dup-${group.query}`,
      kind: `dup-${group.query}`,
      level: "warning",
      where: group.sections.map((s) => `${s.chapterIndex + 1}.${s.sectionIndex + 1}절`).join(" · "),
      message: `검색 질의가 같아서(${group.query}) 이 절들이 같은 자료를 인용하게 됩니다.`,
      target: outlineTarget(first.chapterIndex, first.sectionIndex, "sectionTitle"),
    });
  }

  // 접지 않고 전부 돌려준다 - 편집기는 칸마다 표시해야 하고, 접는 것은 체크리스트
  // 몫이다(foldIssues). 여기서 접으면 한 절만 빨갛고 나머지는 멀쩡해 보인다.
  const ordered = [
    ...issues,
    ...untitledSections,
    ...droppedChapters,
    ...emptyChapterTitles,
    ...blankPoints,
    ...noDirection,
    ...noAnalyst,
  ];
  // 막는 것 먼저 - 목록 맨 위가 곧 "지금 고칠 것"이다(정렬은 안정적이라 같은
  // 등급 안에서는 위에서 만든 문서 순서가 유지된다).
  return [
    ...ordered.filter((i) => i.level === "blocker"),
    ...ordered.filter((i) => i.level === "warning"),
  ];
}

export interface FormIssueInput {
  title: string;
  topic: string;
  rules?: string[];
  /** 목차 편집기가 계산해 올려 준 문제들 - 제목 없는 절은 config.outline에서 이미 지워져 폼에서는 보이지 않는다 */
  outlineIssues: FormIssue[];
}

/** 폼 전체의 문제 목록 - 기본 정보 + 목차. 순서가 곧 "먼저 고칠 것" 순서다. */
export function collectFormIssues({
  title,
  topic,
  rules,
  outlineIssues,
}: FormIssueInput): FormIssue[] {
  const issues: FormIssue[] = [];
  const t = title.trim();
  if (!t) {
    issues.push({
      id: "title-empty",
      kind: "title-empty",
      level: "blocker",
      where: "보고서 제목",
      message: "표지에 실릴 제목을 적으세요.",
      target: { kind: "field", elementId: "pf-title" },
    });
  } else if (t.length > LIMITS.title) {
    issues.push({
      id: "title-long",
      kind: "title-long",
      level: "blocker",
      where: "보고서 제목",
      message: `${LIMITS.title}자를 넘습니다 (현재 ${t.length}자).`,
      target: { kind: "field", elementId: "pf-title" },
    });
  }

  const tp = topic.trim();
  if (!tp) {
    issues.push({
      id: "topic-empty",
      kind: "topic-empty",
      level: "blocker",
      where: "주제",
      message: "무엇을 어떤 관점으로 볼지 적어야 자료를 찾을 수 있습니다.",
      target: { kind: "field", elementId: "pf-topic" },
    });
  } else if (tp.length > LIMITS.topic) {
    issues.push({
      id: "topic-long",
      kind: "topic-long",
      level: "blocker",
      where: "주제",
      message: `${LIMITS.topic}자를 넘습니다 (현재 ${tp.length}자).`,
      target: { kind: "field", elementId: "pf-topic" },
    });
  } else if (tp.length < 15) {
    issues.push({
      id: "topic-short",
      kind: "topic-short",
      level: "warning",
      where: "주제",
      message: "너무 짧습니다. 대상·범위·목적을 한 문장으로 적을수록 수집이 정확해집니다.",
      target: { kind: "field", elementId: "pf-topic" },
    });
  }

  if (rules && rules.length > LIMITS.rules) {
    issues.push({
      id: "rules-many",
      kind: "rules-many",
      level: "blocker",
      where: "작성 규칙",
      message: `규칙은 ${LIMITS.rules}개까지 고를 수 있습니다.`,
      target: { kind: "field", elementId: "pf-rules" },
    });
  }

  const all = [...issues, ...outlineIssues];
  return [...all.filter((i) => i.level === "blocker"), ...all.filter((i) => i.level === "warning")];
}

/** 편집기가 칸마다 빨간 테두리·사유를 붙이려고 쓰는 색인 - 키는 "ci:si:field"("ci:c:field"=장). */
export function indexOutlineIssues(issues: FormIssue[]): Map<string, FormIssue> {
  const map = new Map<string, FormIssue>();
  for (const issue of issues) {
    if (issue.target.kind !== "outline" || issue.paint === false) continue;
    const { chapterIndex, sectionIndex, field } = issue.target;
    const key = `${chapterIndex}:${sectionIndex === null ? "c" : sectionIndex}:${field}`;
    const prev = map.get(key);
    // 한 칸에 여럿이면 blocker가 이긴다 - 먼저 고쳐야 하는 쪽을 보여준다.
    if (!prev || (prev.level === "warning" && issue.level === "blocker")) map.set(key, issue);
  }
  return map;
}

/** 장 머리에 접힌 채로도 보이는 요약 - 접힌 장 안의 문제를 숨기지 않는다.
 * 접지 않은 목록(collectOutlineIssues의 반환값)을 그대로 넣는다. */
export function chapterIssueCounts(
  issues: FormIssue[],
): Map<number, { blockers: number; warnings: number }> {
  const map = new Map<number, { blockers: number; warnings: number }>();
  for (const issue of issues) {
    if (issue.target.kind !== "outline" || issue.paint === false) continue;
    const ci = issue.target.chapterIndex;
    const entry = map.get(ci) ?? { blockers: 0, warnings: 0 };
    if (issue.level === "blocker") entry.blockers += 1;
    else entry.warnings += 1;
    map.set(ci, entry);
  }
  return map;
}
