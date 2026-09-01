// 버전 사유(reason) 해석 - 목록 카드와 상태 패널이 함께 쓴다.
// 서버가 싣는 값: assemble | reopen | finalize | outline | sources
//               | rewrite:<장.절> | block:<장.절> | edit:<장.절> | restore:<장.절>
//               | manual[:<꼬리표>]

/** 버전 사유를 사람 말로. 목록은 "무엇이 왜 바뀌었나"를 읽는 자리지 기계 라벨을
 *  보는 자리가 아니다 - rewrite:2.3만 나열되면 이력이 안 읽힌다. */
export function reasonLabel(reason: string): string {
  if (reason === "assemble") return "완성본";
  if (reason === "reopen") return "다시 열기 직전";
  if (reason === "finalize") return "최종 확정";
  if (reason === "outline") return "목차 수정";
  if (reason === "sources") return "자료 채택 변경 · 인용 번호 조정";
  if (reason.startsWith("rewrite:")) return `AI가 다시 씀 · ${reason.slice("rewrite:".length)}절`;
  if (reason.startsWith("block:")) return `블록만 고침 · ${reason.slice("block:".length)}절`;
  if (reason.startsWith("edit:")) return `직접 고침 · ${reason.slice("edit:".length)}절`;
  // 되돌리기도 덮어쓰기다 - 이 분기가 없어서 "직접 저장"으로 뜨고 있었다(2026-08-27).
  // 파괴적인 행위일수록 이력에서 제 이름으로 보여야 한다.
  if (reason.startsWith("restore:")) return `되돌림 · ${reason.slice("restore:".length)}절`;
  if (reason.startsWith("manual:")) return `직접 저장 · ${reason.slice("manual:".length)}`;
  return "직접 저장";
}

/** 이정표인가 - 문서 전체의 국면이 바뀐 지점(완성·재개·확정·목차·자료)과 사람이
 *  직접 찍은 체크포인트(수동 저장·되돌리기).
 *
 *  절 하나를 손댈 때마다 쌓이는 자동 스냅샷(재작성·블록·직접 편집)은 성격이 다르다:
 *  그건 되돌릴 수 있게 하는 **안전망**이지 사람이 읽는 이력이 아니다. 35절 보고서를
 *  한 바퀴 손보면 그것만 수십 개라 이정표가 묻힌다 - 기본 목록에서 접고 토글로 편다.
 *
 *  되돌리기(restore)는 절 단위지만 이정표로 둔다 - 롤백은 이력에서 찾으려고 보는
 *  바로 그 사건이다. */
export function isMilestoneReason(reason: string): boolean {
  return !(
    reason.startsWith("rewrite:") ||
    reason.startsWith("block:") ||
    reason.startsWith("edit:")
  );
}
