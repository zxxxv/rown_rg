/** 파일 크기 표기 - 기수는 1024 하나로 통일(Windows 탐색기와 같은 값).
 * 화면마다 1000/1024 사본이 갈라져 같은 파일 크기가 다르게 보이던 드리프트 수리. */
export function formatSize(bytes: number): string {
  if (bytes <= 0) return "-";
  const kb = bytes / 1024;
  if (kb < 1024) return `${Math.round(kb)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
}
