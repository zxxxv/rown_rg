// 폴링·디바운스 간격(ms) 이름표 - 값은 각 화면에 흩어져 있던 리터럴 그대로(동작 불변).
// 같은 성격의 폴링이 화면마다 맨 숫자로 적혀 있어, 조정할 때 어디가 같은 축인지
// 찾을 수 없었다 - 이름으로 축을 드러낸다.

/** 단발 작업(절 재작성 배치·복수안 생성) 상태 폴링 */
export const POLL_JOB_MS = 3000;
/** 검증 리포트·상태 폴링 - 챕터당 1콜짜리 작업이라 조금 느슨하게 */
export const POLL_VERIFY_MS = 4000;
/** 수집 중 자료 목록 폴링 */
export const POLL_LIST_MS = 5000;
/** 페이지 수준 진행 스냅샷 폴링(개요·설계·자료·미리보기) */
export const POLL_PAGE_MS = 7000;
/** GPU 모니터 폴링 - 서버 샘플이 5초라 10초면 두 칸 */
export const POLL_GPU_MS = 10_000;
/** 검색 입력 디바운스 */
export const SEARCH_DEBOUNCE_MS = 300;
