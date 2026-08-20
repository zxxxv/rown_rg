/** 목차 편집 중 "이 절은 무엇으로 검색되는가"를 보여주기 위한 미리보기 계산.
 *
 * 규칙은 백엔드 services/retrieval/section.section_search_query와 같다:
 * 장 제목 + 절 제목, 단 장 제목이 절 제목에 이미 들어 있으면 절 제목만.
 *
 * 확정 값은 실행 시작 후 설계 브리프 게이트가 **서버에서** 계산해 보여준다 - 여기는
 * 짜는 동안의 미리보기라, 규칙이 갈라지면 브리프 쪽이 진실이다. 그래도 여기 두는
 * 이유는 중복을 만들고 나서가 아니라 만드는 순간에 알아야 고치기 때문이다.
 *
 * 왜 중요한가(2026-08-14 탄소규제 런 실측): 규제 4종을 같은 5절 틀로 비교하는 정석적인
 * 목차에서 12개 절이 글자까지 같은 질의를 던져 네 장이 같은 자료를 인용했다. 검색은
 * 결정적이라 질의가 같으면 결과도 같다 - 장이 달라도 구별되지 않는 글이 나온다.
 */

export interface QueryPreviewSection {
  chapterIndex: number;
  sectionIndex: number;
  label: string;
  query: string;
}

export interface DuplicateQueryGroup {
  query: string;
  sections: QueryPreviewSection[];
}

interface ChapterLike {
  title: string;
  sections: { title: string }[];
}

/** 이 절의 1차 검색 질의. 제목이 비면 빈 문자열(아직 안 적은 절은 세지 않는다). */
export function effectiveSearchQuery(chapterTitle: string, sectionTitle: string): string {
  const title = sectionTitle.trim();
  if (!title) return "";
  const chapter = chapterTitle.trim();
  if (chapter && !title.toLowerCase().includes(chapter.toLowerCase())) {
    return `${chapter} ${title}`;
  }
  return title;
}

/** 이 절에 배정된 에이전트가 추가하는 검색 질의 미리보기.
 *
 * 백엔드 services/retrieval/section._analyst_queries 규칙의 미러: "{topic}" 자리에
 * 보고서 주제가 아니라 **장·절 제목**이 꽂히고(획일화 방지), 에이전트당 2개,
 * 앵커 80자 캡. 정의는 에이전트(재사용 자산)에 있고 확인은 여기(절)에서 한다 -
 * 확정 값은 실행 후 설계 검토가 서버에서 다시 계산한다. */
export function analystQueryPreviews(
  chapterTitle: string,
  sectionTitle: string,
  analystNames: string[],
  catalog: { name: string; queries: string[] }[],
): string[] {
  if (analystNames.length === 0) return [];
  const anchor = effectiveSearchQuery(chapterTitle, sectionTitle).replace(/\s+/g, " ").slice(0, 80);
  const byName = new Map(catalog.map((a) => [a.name, a]));
  const out: string[] = [];
  for (const name of analystNames) {
    const spec = byName.get(name);
    if (!spec) continue;
    for (const template of spec.queries.slice(0, 2)) {
      const text = template
        .replace(/\{topic\}/g, anchor)
        .replace(/\s+/g, " ")
        .trim();
      if (text && !out.includes(text)) out.push(text);
    }
  }
  return out;
}

/** 같은 질의를 쓰는 절 묶음(2개 이상). 비어 있는 절은 제외한다. */
export function duplicateQueryGroups(chapters: ChapterLike[]): DuplicateQueryGroup[] {
  const byQuery = new Map<string, QueryPreviewSection[]>();
  chapters.forEach((chapter, ci) => {
    chapter.sections.forEach((section, si) => {
      const query = effectiveSearchQuery(chapter.title, section.title);
      if (!query) return;
      const entry = {
        chapterIndex: ci,
        sectionIndex: si,
        label: `${ci + 1}.${si + 1} ${section.title.trim()}`,
        query,
      };
      const bucket = byQuery.get(query);
      if (bucket) bucket.push(entry);
      else byQuery.set(query, [entry]);
    });
  });
  return [...byQuery.entries()]
    .filter(([, sections]) => sections.length > 1)
    .map(([query, sections]) => ({ query, sections }));
}

/** 중복 질의에 걸린 절 좌표 집합 - "ci:si" 키. 해당 절만 붉게 표시하려고. */
export function duplicatedSectionKeys(groups: DuplicateQueryGroup[]): Set<string> {
  const keys = new Set<string>();
  for (const group of groups) {
    for (const s of group.sections) keys.add(`${s.chapterIndex}:${s.sectionIndex}`);
  }
  return keys;
}
