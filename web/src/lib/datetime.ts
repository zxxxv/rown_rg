// 날짜·시각 표기 공용 모듈 - 표시는 KST 고정(저장·계산은 UTC, 여기는 표시 계층).
// 화면마다 같은 포매터 사본이 4벌씩 있었고, 버전 이력만 브라우저 로컬 시간을 써서
// 화면마다 시각이 달랐다 - 그 드리프트를 여기 하나로 수리한다.

/** "2026. 8. 30. 오후 2:22" 꼴 - 관리자 표(마지막 로그인·등록일 등)의 공용 표기. */
export function fmtKstDateTime(iso?: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    dateStyle: "medium",
    timeStyle: "short",
  });
}

/** 날짜만("2026. 8. 30.") - 가입일·변경일처럼 시각이 소음인 자리. */
export function fmtKstDate(iso?: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleDateString("ko-KR", {
    timeZone: "Asia/Seoul",
    dateStyle: "medium",
  });
}

// 최근 수정을 분 단위 KST로 - 날짜만 보이면 같은 날의 편집이 화면에서 안 움직여
// "반영이 안 된다"로 읽힌다(2026-08-31 지적). sv-SE 로케일이 YYYY-MM-DD HH:mm 꼴을 준다.
export function formatKstMinute(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  return d.toLocaleString("sv-SE", { timeZone: "Asia/Seoul" }).slice(0, 16);
}

/** 압축형 "월/일 시:분" - 버전 기록처럼 한 줄에 여럿 늘어서는 자리.
 * 표시는 KST 고정 - 버전 이력만 로컬 시간이라 화면마다 시각이 달랐다. */
export function fmtKstCompact(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const s = d.toLocaleString("sv-SE", { timeZone: "Asia/Seoul" }); // YYYY-MM-DD HH:mm:ss
  return `${Number(s.slice(5, 7))}/${Number(s.slice(8, 10))} ${s.slice(11, 16)}`;
}
