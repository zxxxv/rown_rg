export type ComponentType = "paragraph" | "table" | "figure" | "callout";
export type QAVerdict = "pending" | "passed" | "failed" | "warning";

export interface QAResult {
  fact: QAVerdict;
  consistency: QAVerdict;
  style: QAVerdict;
  critic: QAVerdict;
}

export interface EditorComponent {
  id: string;
  type: ComponentType;
  markdown: string;
  src_ids: string[];
  confidence: number;
  qa: QAResult;
  /** 같은 출처를 공유하는 다른 섹션. UI에서 “연결된 섹션” 표시용. */
  cross_references: { section_id: string; section_title: string }[];
}

export interface EditorSample {
  section_id: string;
  section_title: string;
  components: EditorComponent[];
}

const OK_QA: QAResult = {
  fact: "passed",
  consistency: "passed",
  style: "passed",
  critic: "passed",
};

export const EDITOR_SAMPLE: EditorSample = {
  section_id: "2.3",
  section_title: "인구·고령화 영향 + 비용편익비",
  components: [
    {
      id: "comp_1",
      type: "paragraph",
      markdown:
        "본 사업의 수요는 수도권 인구 구조의 변화에 직접적인 영향을 받는다. 통계청의 2024년 인구주택총조사에 따르면 **65세 이상 고령 인구의 비율은 19.2%**로 사상 최고를 기록했으며, 수도권 거주 인구는 전체의 **50.7%**에 달한다.",
      src_ids: ["src_kostat_2024"],
      confidence: 0.93,
      qa: OK_QA,
      cross_references: [
        { section_id: "1.1", section_title: "사업 배경" },
        { section_id: "3.3", section_title: "비용편익비 (B/C)" },
      ],
    },
    {
      id: "comp_2",
      type: "table",
      markdown:
        "### 인구 추계 (2024 → 2030)\n\n| 지표 | 2024년 | 2030년 (전망) | 변동 |\n|---|---|---|---|\n| 65세 이상 인구 비율 | 19.2% | 25.5% | +6.3%p |\n| 수도권 인구 집중도 | 50.7% | 53.0% | +2.3%p |\n| 잠재성장률 | 2.1% | 1.5% | −0.6%p |",
      src_ids: ["src_kostat_2024", "src_kdi_aging"],
      confidence: 0.91,
      qa: OK_QA,
      cross_references: [{ section_id: "2.2", section_title: "수요 분석" }],
    },
    {
      id: "comp_3",
      type: "paragraph",
      markdown:
        "KDI의 거시 영향 분석은 2030년 잠재성장률을 **1.5%**로 둔화될 것으로 전망한다. 이는 출퇴근 수요를 약 8% 감소시키는 한편, 여가·의료 통행은 연 12% 증가시킬 것으로 추정된다. 본 사업의 비용편익 분석은 이러한 분해 효과를 반영해야 한다.",
      src_ids: ["src_kdi_aging"],
      confidence: 0.87,
      qa: OK_QA,
      cross_references: [{ section_id: "3.2", section_title: "편익 추정" }],
    },
    {
      id: "comp_4",
      type: "table",
      markdown:
        "### B/C 추정 결과 (30년 운영)\n\n| 항목 | 현재가치 (조원) | 비고 |\n|---|---|---|\n| 총 비용 (C) | 18.2 | 사업비 + 30년 운영비 |\n| 총 편익 (B) | 22.4 | 통행시간 + 운영비 절감 + 환경 |\n| **B/C 비율** | **1.23** | 권고 기준 1.0 통과 |\n| NPV | 4.2 | (B − C) 현재가치 |\n| IRR | 7.8% | 사회적 할인율 초과 |",
      src_ids: ["src_moef_preliminary", "src_bok_econ"],
      confidence: 0.94,
      qa: OK_QA,
      cross_references: [{ section_id: "3.3", section_title: "비용편익비 (B/C)" }],
    },
    {
      id: "comp_5",
      type: "paragraph",
      markdown:
        "종합적으로 본 사업은 사회적 할인율 4.5% 기준 B/C **1.23**으로 권고 기준을 충족한다. 다만 감사원의 GTX 사업 효과 평가에서 지적한 **비용 초과 1.8조원** 사례를 고려하여, 사업비 변동에 대한 추가 대응 계획을 권고한다.",
      src_ids: ["src_kostat_2024", "src_kdi_aging"],
      confidence: 0.89,
      qa: { fact: "passed", consistency: "passed", style: "passed", critic: "warning" },
      cross_references: [{ section_id: "5.2", section_title: "결론" }],
    },
  ],
};
