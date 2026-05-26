import type { ChapterNode, SectionStatus } from "@/api/types";

interface NodeSpec {
  id: string;
  title: string;
  status: SectionStatus;
  children: { id: string; title: string; status: SectionStatus }[];
}

const SECTION_SPECS: NodeSpec[] = [
  {
    id: "1",
    title: "사업 개요",
    status: "completed",
    children: [
      { id: "1.1", title: "사업 배경", status: "completed" },
      { id: "1.2", title: "사업 목적", status: "completed" },
      { id: "1.3", title: "사업 범위", status: "completed" },
    ],
  },
  {
    id: "2",
    title: "사업 환경 분석",
    status: "writing",
    children: [
      { id: "2.1", title: "거시 환경 (STEEP)", status: "completed" },
      { id: "2.2", title: "수요 분석", status: "completed" },
      { id: "2.3", title: "인구·고령화 영향", status: "completed" },
    ],
  },
  {
    id: "3",
    title: "경제성 분석",
    status: "writing",
    children: [
      { id: "3.1", title: "비용 추정", status: "completed" },
      { id: "3.2", title: "편익 추정", status: "writing" },
      { id: "3.3", title: "비용편익비 (B/C)", status: "completed" },
    ],
  },
  {
    id: "4",
    title: "정책성 분석",
    status: "pending",
    children: [
      { id: "4.1", title: "정책 일관성", status: "pending" },
      { id: "4.2", title: "지역균형", status: "pending" },
      { id: "4.3", title: "환경 영향", status: "writing" },
    ],
  },
  {
    id: "5",
    title: "종합 평가",
    status: "pending",
    children: [
      { id: "5.1", title: "AHP 종합 점수", status: "pending" },
      { id: "5.2", title: "결론", status: "pending" },
      { id: "5.3", title: "권고사항", status: "pending" },
    ],
  },
];

export const SECTION_TREE: ChapterNode[] = SECTION_SPECS.map((s) => ({
  id: s.id,
  title: s.title,
  level: 1 as const,
  status: s.status,
  children: s.children.map((c) => ({
    id: c.id,
    title: c.title,
    level: 2 as const,
    status: c.status,
    parent_id: s.id,
  })),
}));

export function findSectionStatus(sectionId: string): SectionStatus | null {
  for (const chapter of SECTION_TREE) {
    if (chapter.id === sectionId) return chapter.status;
    for (const section of chapter.children) {
      if (section.id === sectionId) return section.status;
    }
  }
  return null;
}

export function findSectionTitle(sectionId: string): string | null {
  for (const chapter of SECTION_TREE) {
    if (chapter.id === sectionId) return chapter.title;
    for (const section of chapter.children) {
      if (section.id === sectionId) return section.title;
    }
  }
  return null;
}
