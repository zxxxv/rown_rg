import type { SectionContentResponse } from "@/api/types";

export const RICH_2_3 = `## 2.3 인구·고령화 영향

본 사업의 수요는 수도권 인구 구조의 변화에 직접적인 영향을 받는다. 통계청의 2024년 인구주택총조사 표본 집계 결과에 따르면 65세 이상 고령 인구의 비율은 **19.2%**로 사상 최고를 기록했으며, 수도권 거주 인구는 전체의 **50.7%** [1]에 달한다. KDI의 거시 영향 분석은 2030년 잠재성장률을 **1.5%**로 둔화될 것으로 전망한다 [2].

### 주요 통계

| 지표 | 2024년 | 2030년 (전망) | 변동 |
|---|---|---|---|
| 65세 이상 인구 비율 | 19.2% | 25.5% | +6.3%p |
| 수도권 인구 집중도 | 50.7% | 53.0% | +2.3%p |
| 잠재성장률 | 2.1% | 1.5% | −0.6%p |

### 사업 수요에 미치는 영향

1. **출퇴근 수요 감소**: 생산가능인구의 감소는 광역교통망의 출퇴근 수요를 2030년 기준 약 8% 감소시킬 것으로 추정된다.
2. **여가·의료 통행 증가**: 반면 고령자의 여가·의료 통행은 연 평균 12% 증가가 예상된다 [2].
3. **수도권 집중 지속**: 지방소멸 위험지역이 전국 시·군·구의 **49.6%** [3]에 달하면서 수도권 통행은 지속 증가 추세.

> 본 사업의 비용편익 분석은 이러한 인구 구조 변화를 반영하여 수요 추정 모델을 보정해야 한다. 단순 외삽이 아닌 **세대별·통행 목적별 분해 모델**이 필요하다.
`;

export const RICH_3_3 = `## 3.3 비용편익비 (B/C)

본 사업의 비용편익 분석은 30년 운영 기간을 기준으로 한다. 기획재정부 예비타당성조사 운용 지침에 따라 사회적 할인율 **4.5%**를 적용하며, B/C 비율 **1.0 이상**을 권고 조건으로 한다 [1].

### 비용·편익 추정 결과

| 항목 | 현재가치 (조원) | 비고 |
|---|---|---|
| 총 비용 (C) | 18.2 | 사업비 + 운영비 30년 |
| 총 편익 (B) | 22.4 | 통행시간 + 운영비 절감 + 환경 |
| **B/C 비율** | **1.23** | 권고 통과 |
| NPV | 4.2 | (B − C) 현재가치 |
| IRR | 7.8% | 사회적 할인율 초과 |

### 민감도 분석

- 통행 수요가 **−10%** 변화 시 B/C는 1.10으로 감소 (여전히 1.0 이상)
- 사업비가 **+15%** 증가 시 B/C는 1.07로 감소
- 사회적 할인율 **5.5%** 적용 시 B/C는 1.05로 감소

> 한국은행의 2025년 경제성장률 전망 **2.1%** [2]을 반영하더라도 본 사업은 사업타당성 기준을 충족한다. 다만 감사원의 GTX 사업 효과 평가에서 지적한 **비용 초과 1.8조원** 사례 [3]를 고려하여 사업비 변동에 대한 추가 대응 계획이 필요하다.

### 권고

본 사업은 사회적 할인율 기준 B/C 1.23으로 사업타당성 기준을 충족하며, 정책성·지역균형 평가와 결합한 AHP 종합점수가 **0.5 이상**일 경우 예비타당성을 통과할 수 있다.
`;

const PLACEHOLDER = (id: string, title: string) =>
  `## ${id} ${title}\n\n이 섹션은 작성 완료 상태이지만 시연용 본문은 아직 준비되지 않았습니다. 실제 보고서에서는 1500~3000자 분량의 본문이 표시됩니다.\n\n표·각주·인용·출처 마커 등은 위쪽의 \`2.3\` 또는 \`3.3\`을 참고하세요.`;

const CONTENTS: Record<string, Omit<SectionContentResponse, "id" | "title">> = {
  "2.3": {
    content: RICH_2_3,
    source_ids: ["src_kostat_2024", "src_kdi_aging", "src_audit_gtx"],
    qa_status: "passed",
    level: 2,
    ungrounded: { count: 0, samples: [] },
    evidence: { count: 24, scarce: false },
    citations: [
      {
        number: 1,
        title: "2024 인구주택총조사 표본 집계 결과",
        url: "https://kostat.go.kr/",
        source_id: "src_kostat_2024",
        reliability: "high",
      },
      {
        number: 2,
        title: "고령화 진전이 노동시장에 미치는 영향",
        url: "https://kdi.re.kr/",
        source_id: "src_kdi_aging",
        reliability: "high",
      },
      {
        number: 3,
        title: "GTX 사업 효과 평가",
        url: null,
        source_id: "src_audit_gtx",
        reliability: "high",
      },
    ],
  },
  "3.3": {
    content: RICH_3_3,
    source_ids: ["src_moef_preliminary", "src_bok_econ", "src_audit_gtx"],
    qa_status: "passed",
    level: 2,
    ungrounded: { count: 0, samples: [] },
    evidence: { count: 24, scarce: false },
    citations: [
      {
        number: 1,
        title: "공공투자 예비타당성조사 운용 지침",
        url: "https://moef.go.kr/",
        source_id: "src_moef_preliminary",
        reliability: "high",
      },
      {
        number: 2,
        title: "한국은행 경제전망보고서 2024-IV",
        url: "https://bok.or.kr/",
        source_id: "src_bok_econ",
        reliability: "high",
      },
      {
        number: 3,
        title: "GTX 사업 효과 평가",
        url: null,
        source_id: "src_audit_gtx",
        reliability: "high",
      },
    ],
  },
};

export function buildSectionContent(
  sectionId: string,
  title: string,
): SectionContentResponse | null {
  const rich = CONTENTS[sectionId];
  if (rich) {
    return { id: sectionId, title, ...rich };
  }
  return {
    id: sectionId,
    title,
    content: PLACEHOLDER(sectionId, title),
    source_ids: [],
    qa_status: "passed",
    level: sectionId.includes(".") ? 2 : 1,
    ungrounded: { count: 0, samples: [] },
    evidence: { count: 24, scarce: false },
    citations: [],
  };
}
