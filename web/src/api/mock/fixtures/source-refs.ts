import type { Source } from "@/api/types";

type SourceRef = Omit<Source, "project_id"> & { project_id?: string };

const REFS: Record<string, SourceRef> = {
  src_kostat_2024: {
    id: "src_kostat_2024",
    title: "2024 인구주택총조사 표본 집계 결과",
    source: "통계청",
    source_kind: "gov",
    url: "https://kostat.go.kr/",
    published_at: "2024-09-15",
    pages: 156,
    reliability: 0.97,
    summary: "전국 시·군 단위 인구·가구·주택 통계. 65세 이상 인구 비중 19.2%, 수도권 집중도 50.7%.",
    is_included: true,
    quotes: [
      "65세 이상 고령 인구 비율은 19.2%로 전년 대비 1.0%p 상승했다.",
      "수도권에 거주하는 인구는 전체의 50.7%로 집계됐다.",
    ],
  },
  src_kdi_aging: {
    id: "src_kdi_aging",
    title: "고령화 진전이 노동시장에 미치는 영향",
    source: "한국개발연구원(KDI)",
    source_kind: "academic",
    url: "https://kdi.re.kr/",
    published_at: "2023-12-04",
    pages: 64,
    reliability: 0.92,
    summary:
      "생산가능인구 감소가 잠재성장률·임금·복지 지출에 미치는 거시 영향. 2030년 잠재성장률 1.5% 전망.",
    is_included: true,
    quotes: ["2030년 잠재성장률은 1.5%로 둔화될 전망이다."],
  },
  src_audit_gtx: {
    id: "src_audit_gtx",
    title: "GTX 사업 효과 평가",
    source: "감사원",
    source_kind: "gov",
    published_at: "2024-03-11",
    pages: 78,
    reliability: 0.93,
    summary: "GTX-A·B·C 노선의 비용편익·사업관리·재원조달 적정성 감사. 비용 초과 1.8조원 지적.",
    is_included: true,
    quotes: ["전체 사업비 대비 비용 초과는 1.8조원에 달했다."],
  },
  src_moef_preliminary: {
    id: "src_moef_preliminary",
    title: "공공투자 예비타당성조사 운용 지침",
    source: "기획재정부",
    source_kind: "gov",
    url: "https://moef.go.kr/",
    published_at: "2024-02-08",
    pages: 42,
    reliability: 0.96,
    summary: "예타조사 절차·평가 항목·통과 기준. B/C·정책성·지역균형 가중치 0.5·0.25·0.25.",
    is_included: true,
    quotes: ["B/C 1.0 이상이 권고 조건이며, AHP 종합점수 0.5 이상이어야 한다."],
  },
  src_bok_econ: {
    id: "src_bok_econ",
    title: "한국은행 경제전망보고서 2024-IV",
    source: "한국은행",
    source_kind: "gov",
    url: "https://bok.or.kr/",
    published_at: "2024-11-28",
    pages: 138,
    reliability: 0.95,
    summary: "2025년 경제성장률 2.1% 전망. 수출 회복·내수 둔화·물가 안정.",
    is_included: true,
    quotes: ["2025년 한국 경제는 2.1% 성장할 것으로 전망된다."],
  },
};

export function findSourceRef(srcId: string): Source | null {
  const ref = REFS[srcId];
  if (!ref) return null;
  return { ...ref, project_id: ref.project_id ?? "demo" };
}
