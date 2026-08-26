// 목 세션 동안만 사는 절 상태 - 잠금과 미반영 목록.
//
// 두 핸들러(sections·projects)가 같은 값을 봐야 화면이 앞뒤가 맞는다: 본문 화면에서
// 절을 잠그면 개요의 미반영 카드에도 '잠김'이 떠야 하고, 카드에서 "이대로 두기"를
// 누르면 그 줄이 사라져야 한다. 한쪽에만 두면 버튼이 아무 일도 안 하는 것처럼 보인다.
// 새로고침하면 초기값으로 돌아간다(목의 성질 - 실백엔드는 DB가 기억한다).

/** 절 id -> 잠금. 잠긴 절은 AI 재작성 경로가 막힌다(실백엔드 0048과 같은 계약). */
export const SECTION_LOCKS = new Map<string, boolean>();

export type MockDriftRow = {
  section_id: string;
  label: string;
  reasons: string[];
  excluded_sources: { id: string; title: string }[];
};

/** 미반영 절 - "이대로 두기"로 지워지고, 다시 쓰면(목에서는 생략) 사라진다. */
export const DRIFT_ROWS = new Map<string, MockDriftRow>([
  [
    "2.3",
    {
      section_id: "2.3",
      label: "2.3 인구·고령화 영향",
      reasons: ["plan_changed"],
      excluded_sources: [],
    },
  ],
  [
    "3.3",
    {
      section_id: "3.3",
      label: "3.3 비용편익비 (B/C)",
      reasons: ["source_excluded"],
      excluded_sources: [{ id: "src_audit_gtx", title: "GTX 사업 효과 평가" }],
    },
  ],
]);
