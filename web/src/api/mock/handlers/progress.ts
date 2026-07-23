import { HttpResponse, http } from "msw";
import { DEMO_PROJECTS } from "@/api/mock/fixtures/projects";
import { getAnyRunnerState } from "@/api/mock/fixtures/scenarios/state";
import type { PhaseName } from "@/api/ws-messages";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

// 시나리오 phase → 백엔드 ProjectStage
const PHASE_TO_STATUS: Record<PhaseName, string> = {
  research: "researching",
  indexing: "indexing",
  writing: "writing",
  qa: "reviewing",
  export: "completed",
};

// 백엔드 routers/projects.py _STAGE_PERCENT 미러 (단계 기반 근사 진행률)
const STAGE_PERCENT: Record<string, number> = {
  created: 0,
  researching: 20,
  indexing: 40,
  writing: 60,
  reviewing: 85,
  completed: 100,
  archived: 100,
};

// 실계약 미러: write_loop.qa_select_payload — 섹션별 HARD 통과 후보 + SOFT 경고.
// UUID는 데모용 고정값(선택 라운드트립 확인용).
const QA_SECTION_1 = "0f000000-0000-4000-8000-000000000001";
const QA_SECTION_2 = "0f000000-0000-4000-8000-000000000002";
const QA_SECTION_3 = "0f000000-0000-4000-8000-000000000003";

const QA_SELECT_PAYLOAD = {
  message: "섹션별로 후보를 하나씩 고르세요. (정적검사 통과분만 표시)",
  section_plan: [
    {
      section_id: QA_SECTION_1,
      chapter_number: 2,
      section_number: 3,
      title: "인구·고령화 영향",
      direction: "지역 인구구조 변화가 수요 추정에 미치는 영향을 정량 중심으로 정리",
      key_points: ["고령화율 추이", "생산가능인구 감소", "수요 추정 반영 방식"],
      analysts: ["수요분석"],
    },
    {
      section_id: QA_SECTION_2,
      chapter_number: 3,
      section_number: 3,
      title: "비용편익비 (B/C)",
      direction: "비용·편익 산정 근거와 민감도 분석을 표 중심으로 제시",
      key_points: ["B/C 산출 근거", "할인율 민감도"],
      analysts: ["비용편익분석"],
    },
    {
      section_id: QA_SECTION_3,
      chapter_number: 4,
      section_number: 1,
      title: "위험 요인 및 대응",
      direction: "",
      key_points: [],
      analysts: ["위험분석"],
    },
  ],
  sections: [
    {
      section_id: QA_SECTION_1,
      all_excluded: false,
      candidates: [
        {
          candidate_id: "0c000000-0000-4000-8000-00000000a101",
          content:
            "□ 지역 인구구조 변화 개요\n ㅇ 대상 지역 고령화율은 24.1%로 전국 평균(19.2%) 대비 4.9%p 높음 [1]\n  - 2030년 28.7% 도달 전망으로 수요 기반 축소 예상 [2]\n ㅇ 생산가능인구는 최근 5년간 연평균 1.8% 감소 [1]\n\n□ 수요 추정 반영\n ㅇ 코호트 요인법 기반 장래인구추계를 수요 모형의 기초 입력으로 사용 [3]\n  - 고령층 이용 비중이 높은 시설 특성상 보수적 추정 적용 [2]",
          cited_chunk_ids: ["c1", "c2", "c3"],
          warnings: [],
        },
        {
          candidate_id: "0c000000-0000-4000-8000-00000000a102",
          content:
            "□ 인구·고령화 현황\n ㅇ 고령화율 24.1%, 전국 평균 대비 높은 수준 [1]\n ㅇ 청년층 순유출 지속으로 중장기 수요 하방 압력 존재 [2]\n\n□ 시사점\n ㅇ 수요 추정 시 인구 감소 시나리오를 기본안으로 채택할 필요 [2]",
          cited_chunk_ids: ["c1", "c2"],
          warnings: [{ check: "bounds", detail: "본문 812자 — 권장 최소 1,000자 미달" }],
        },
      ],
    },
    {
      section_id: QA_SECTION_2,
      all_excluded: false,
      candidates: [
        {
          candidate_id: "0c000000-0000-4000-8000-00000000a201",
          content:
            "□ 비용편익 분석 결과\n ㅇ 총편익 1,842억 원, 총비용 1,510억 원으로 B/C 1.22 산출 [1]\n  - 사회적 할인율 4.5% 적용 기준 [2]\n ㅇ 민감도 분석: 할인율 5.5% 적용 시 B/C 1.08로 하락 [2]\n  - 편익 10% 감소 시나리오에서도 1.0 이상 유지 [3]",
          cited_chunk_ids: ["c4", "c5", "c6"],
          warnings: [],
        },
        {
          candidate_id: "0c000000-0000-4000-8000-00000000a202",
          content:
            "□ B/C 산출 개요\n ㅇ 편익 항목: 이용자 편익, 지역경제 파급 효과 중심 구성 [1]\n ㅇ B/C 1.22로 경제적 타당성 확보 판단 [1]\n\n□ 한계\n ㅇ 운영비 추정의 불확실성이 커 보수적 해석 필요 [2]",
          cited_chunk_ids: ["c4", "c5"],
          warnings: [
            { check: "numeric_grounded", detail: "근거에 없는 수치 1건 감지: '파급 효과 3.2배'" },
          ],
        },
      ],
    },
    {
      section_id: QA_SECTION_3,
      all_excluded: true,
      candidates: [],
    },
  ],
};

export const progressHandlers = [
  // 실계약: GET /projects/{id}/progress
  //   → {project_id, status, pending_gate|null, percent, tokens_used, cost_usd}
  http.get(url("projects/:id/progress"), ({ params }) => {
    const projectId = String(params.id);
    const state = getAnyRunnerState(projectId);

    if (!state) {
      // 프론트 status 어휘 = 백엔드 ProjectStage — 픽스처 값을 그대로 통과시킨다.
      const project = DEMO_PROJECTS.find((p) => p.id === projectId);
      const status = project?.status ?? "researching";
      return HttpResponse.json(
        {
          data: {
            project_id: projectId,
            status,
            pending_gate: null,
            percent: STAGE_PERCENT[status] ?? 0,
            tokens_used: 0,
            cost_usd: 0,
          },
        },
        { status: 200 },
      );
    }

    const status =
      state.finished && state.completed_phases.includes("export")
        ? "completed"
        : (PHASE_TO_STATUS[state.active_phase] ?? "researching");
    const isQaSelect = state.active_phase === "qa";
    const pending_gate = state.pending_checkpoint_id
      ? {
          review_point_id: state.pending_checkpoint_id,
          gate: isQaSelect ? "qa_select" : "source_pool",
          payload: isQaSelect ? QA_SELECT_PAYLOAD : {},
        }
      : null;

    return HttpResponse.json(
      {
        data: {
          project_id: projectId,
          status,
          pending_gate,
          percent: STAGE_PERCENT[status] ?? 0,
          tokens_used: state.tokens_used,
          cost_usd: state.cost_usd,
        },
      },
      { status: 200 },
    );
  }),
];
