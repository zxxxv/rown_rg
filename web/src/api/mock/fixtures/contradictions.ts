import type { Contradiction, ContradictionWinner } from "@/api/types";

const TEMPLATES: Omit<Contradiction, "id" | "project_id">[] = [
  {
    subject: "2023년 65세 이상 인구 비중",
    value_a: "17.5%",
    value_b: "17.7%",
    source_a: {
      source_id: "src_kostat_2024_03",
      title: "2024년 3월 인구동향",
      source: "통계청",
      source_kind: "gov",
      published_at: "2024-03-12",
      organization: "통계청 사회통계국",
      page_number: 14,
      reliability: 0.95,
      quote: "2023년 12월 기준 65세 이상 고령 인구는 전체의 17.5%로 집계됐다.",
    },
    source_b: {
      source_id: "src_kdi_2024_08",
      title: "고령화와 노동시장 보고서",
      source: "한국개발연구원(KDI)",
      source_kind: "academic",
      published_at: "2024-08-04",
      organization: "한국개발연구원 노동시장연구부",
      page_number: 32,
      reliability: 0.91,
      quote: "2023년 연간 평균 65세 이상 인구는 17.7%로, 전년 대비 0.9%p 증가했다.",
    },
    status: "pending",
    created_at: "2026-05-22T09:14:00Z",
  },
  {
    subject: "OO지역 GDP 성장률 2023",
    value_a: "2.6%",
    value_b: "2.4%",
    source_a: {
      source_id: "src_bok_regional",
      title: "지역경제 동향 2024-02",
      source: "한국은행",
      source_kind: "gov",
      published_at: "2024-02-20",
      organization: "한국은행 조사국",
      page_number: 8,
      reliability: 0.94,
      quote: "OO지역의 2023년 실질 GDP 성장률은 2.6%로 전국 평균을 상회했다.",
    },
    source_b: {
      source_id: "src_imf_weo_2024",
      title: "World Economic Outlook 2024",
      source: "International Monetary Fund",
      source_kind: "academic",
      published_at: "2024-04-15",
      organization: "IMF Research Department",
      page_number: 142,
      reliability: 0.88,
      quote:
        "Real GDP growth for OO region in 2023 is estimated at 2.4%, slightly below national trend.",
    },
    status: "pending",
    created_at: "2026-05-22T09:16:00Z",
  },
  {
    subject: "예비타당성 평가 기준 변경 시점",
    value_a: "2024년 6월부터",
    value_b: "2024년 7월부터 단계적",
    source_a: {
      source_id: "src_moef_press_2024_05",
      title: "예비타당성조사 운용 지침 개정 보도자료",
      source: "기획재정부",
      source_kind: "gov",
      published_at: "2024-05-09",
      organization: "기획재정부 재정관리국",
      page_number: 3,
      reliability: 0.93,
      quote: "개정된 운용 지침은 2024년 6월 1일부터 신규 예비타당성조사 사업에 적용된다.",
    },
    source_b: {
      source_id: "src_audit_2024_07",
      title: "공공투자 예비타당성조사 정책 검토",
      source: "감사원",
      source_kind: "gov",
      published_at: "2024-07-18",
      organization: "감사원 공공투자감사단",
      page_number: 27,
      reliability: 0.92,
      quote:
        "개정 지침은 2024년 7월부터 단계적으로 적용되며, 진행 중인 사업에는 종전 기준이 유지된다.",
    },
    status: "pending",
    created_at: "2026-05-22T09:20:00Z",
  },
];

const byProject = new Map<string, Contradiction[]>();

export function getContradictionsForProject(projectId: string): Contradiction[] {
  let existing = byProject.get(projectId);
  if (!existing) {
    existing = TEMPLATES.map((tpl, i) => ({
      ...tpl,
      id: `con_${projectId}_${String(i + 1).padStart(3, "0")}`,
      project_id: projectId,
    }));
    byProject.set(projectId, existing);
  }
  return existing;
}

export function resolveContradiction(
  projectId: string,
  cid: string,
  winner: ContradictionWinner,
  resolvedBy: string,
): Contradiction | null {
  const list = getContradictionsForProject(projectId);
  const idx = list.findIndex((c) => c.id === cid);
  if (idx < 0) return null;
  const current = list[idx];
  if (!current) return null;
  const updated: Contradiction = {
    ...current,
    status: "resolved",
    resolution: {
      winner,
      resolved_at: new Date().toISOString(),
      resolved_by: resolvedBy,
    },
  };
  list[idx] = updated;
  return updated;
}
