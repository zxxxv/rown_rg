// 본문 속 차트 스펙 파싱 - 백엔드 src/core/charts.py와 같은 문법을 쓴다.
// 두 렌더러가 같은 스펙을 읽어야 화면에서 본 그래프와 받은 한글 파일이 어긋나지 않는다.

export type ChartType = "bar" | "line" | "pie";

export interface ChartSeries {
  name: string;
  values: number[];
}

export interface ChartSpec {
  type: ChartType;
  title: string;
  unit: string;
  x: string[];
  series: ChartSeries[];
  source: number[];
  /** 변환 전 원본 표(마크다운) - "표로 되돌리기"와 못 그릴 때의 안전망. */
  table: string;
}

// 계열 색 - 백엔드 charts.SERIES_COLORS와 같은 값·같은 순서. 돌려쓰지 않는다.
// 검증 통과: 인접쌍 색각이상 ΔE 9.1, 정상시야 19.6. 대비가 낮은 색이 있어 값 라벨을
// 항상 찍고 원본 표를 함께 보관하는 것이 완화 조건이다.
export const SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"];
export const MAX_SERIES = SERIES_COLORS.length;

// 계열 색 위에 얹는 글자색 두 벌 - 백엔드 charts.LABEL_INK_DARK와 같은 값이어야 한다.
// 다르면 같은 조각을 화면은 흰 글자로, 한글 파일은 검은 글자로 그린다.
const LABEL_INK_DARK = "#2d2d2a";

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => Number.parseInt(hex.slice(i, i + 2), 16) / 255);
  const lin = (c: number) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

/** 이 색 위에 얹을 글자색 - 흰색과 짙은 색 중 대비가 높은 쪽(백엔드 charts.label_ink와 같은 식).
 *  밝기 임계로 가르면 노랑처럼 경계에 걸친 색에서 대비 2.2:1짜리 흰 글자가 남는다. */
export function labelInk(hex: string): string {
  const l = luminance(hex);
  return 1.05 / (l + 0.05) >= (l + 0.05) / (luminance(LABEL_INK_DARK) + 0.05)
    ? "white"
    : LABEL_INK_DARK;
}

const CHART_TYPES = new Set<string>(["bar", "line", "pie"]);
// 값 구분자는 '|' - 한글 수치는 "3,200억"처럼 천 단위 콤마를 달고 살아 콤마를 못 쓴다.
const NUMBER_RE = /-?\d[\d,]*(?:\.\d+)?/;

export class ChartSpecError extends Error {}

function splitList(value: string): string[] {
  return value
    .split("|")
    .map((p) => p.trim())
    .filter(Boolean);
}

// 음수를 나타내는 앞 기호. 한글 보고서는 마이너스를 '△'로 적는다(정부·한국은행 표기 관례).
// 안 읽으면 "수입액 △934"가 +934로 그려져 감소가 증가로 뒤집힌다(백엔드와 같은 목록).
const NEGATIVE_SIGNS = ["△", "▽", "−", "–", "-"];

/** 값 하나로 읽히면 숫자, 아니면 null. "△36"은 -36으로 읽는다.
 *  단위·기호가 붙어 있어도("120억", "3.2%") 숫자 부분만 읽는다. */
export function tryNumber(token: string): number | null {
  const m = NUMBER_RE.exec(token);
  if (m === null) return null;
  const value = Number(m[0].replace(/,/g, ""));
  const prefix = token.slice(0, m.index).trim();
  return NEGATIVE_SIGNS.some((s) => prefix.endsWith(s)) ? -Math.abs(value) : value;
}

function parseNumber(token: string): number {
  const value = tryNumber(token);
  if (value === null) throw new ChartSpecError(`숫자로 읽을 수 없는 값: ${token}`);
  return value;
}

/** 차트 펜스 본문 -> ChartSpec. 그릴 수 없으면 ChartSpecError. */
export function parseChartSpec(body: string): ChartSpec {
  const fields = new Map<string, string>();
  const seriesLines: string[] = [];
  const lines = body.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const m = /^([a-z_]+)[ \t]*:[ \t]*(.*)$/.exec(lines[i].trim());
    if (m === null) continue;
    const [, key, value] = m;
    if (value === "|") {
      // 여러 줄 블록(원본 표) - 들여쓴 줄이 이어지는 동안 모은다.
      const block: string[] = [];
      i++;
      while (i < lines.length && (lines[i].trim() === "" || /^[ \t]/.test(lines[i]))) {
        block.push(lines[i].trim());
        i++;
      }
      i--;
      fields.set(key, block.join("\n").trim());
    } else if (key === "series") {
      seriesLines.push(value);
    } else {
      fields.set(key, value);
    }
  }

  const type = (fields.get("type") ?? "").toLowerCase();
  if (!CHART_TYPES.has(type)) throw new ChartSpecError(`지원하지 않는 차트 종류: ${type}`);

  const x = splitList(fields.get("x") ?? "");
  if (x.length < 2) throw new ChartSpecError("x축 항목이 2개 미만이라 그릴 수 없음");

  const series: ChartSeries[] = [];
  for (const raw of seriesLines) {
    const m = /^(.+?)[ \t]*=[ \t]*(.+)$/.exec(raw);
    if (m === null) throw new ChartSpecError(`계열 형식이 '이름 = 값 | 값'이 아님: ${raw}`);
    const values = splitList(m[2]).map(parseNumber);
    if (values.length !== x.length) {
      throw new ChartSpecError(
        `계열 '${m[1].trim()}'의 값 ${values.length}개가 x축 ${x.length}개와 맞지 않음`,
      );
    }
    series.push({ name: m[1].trim(), values });
  }
  if (series.length === 0) throw new ChartSpecError("계열이 없어 그릴 수 없음");
  if (series.length > MAX_SERIES) {
    throw new ChartSpecError(`계열이 ${series.length}개로 상한 ${MAX_SERIES}개를 넘음`);
  }
  if (type === "pie") {
    if (series.length !== 1) throw new ChartSpecError("원형 차트는 계열이 하나여야 함");
    // 원형은 조각 하나가 색 하나 - 상한을 넘으면 색이 돌아 다른 조각이 같은 색이 된다.
    if (x.length > MAX_SERIES) {
      throw new ChartSpecError(`원형 차트 조각이 ${x.length}개로 상한 ${MAX_SERIES}개를 넘음`);
    }
  }

  const source = (fields.get("source") ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter((s) => /^\d+$/.test(s))
    .map(Number);

  return {
    type: type as ChartType,
    title: fields.get("title") ?? "",
    unit: fields.get("unit") ?? "",
    x,
    series,
    source,
    table: fields.get("table") ?? "",
  };
}

// 못 그리는 차트에서도 원본 표만은 건져 내기 위한 최소 추출 - 'table: |' 뒤 들여쓴 블록.
// 백엔드 report.py _CHART_TABLE_RE와 같은 규칙이다. 스펙이 깨져 파서가 실패해도 이건 읽힌다.
const FALLBACK_TABLE_RE = /^table:[ \t]*\|[ \t]*\n((?:[ \t]+.*\n?)+)/m;

/** 차트 블록에 보관된 원본 표(마크다운) - 없으면 빈 문자열. 되돌리기·폴백이 쓴다. */
export function chartFallbackTable(block: string): string {
  const m = FALLBACK_TABLE_RE.exec(block);
  if (m === null) return "";
  return m[1]
    .split("\n")
    .map((line) => line.replace(/^[ \t]{1,2}/, ""))
    .join("\n")
    .trim();
}

/** ChartSpec -> 펜스 본문(``` 줄 없이). 미리보기 렌더러가 받는 형태와 같다. */
export function toFenceBody(spec: ChartSpec): string {
  const lines = [`type: ${spec.type}`, `title: ${spec.title}`];
  if (spec.unit) lines.push(`unit: ${spec.unit}`);
  lines.push(`x: ${spec.x.join(" | ")}`);
  for (const s of spec.series) lines.push(`series: ${s.name} = ${s.values.join(" | ")}`);
  if (spec.source.length > 0) lines.push(`source: ${spec.source.join(", ")}`);
  if (spec.table) {
    lines.push("table: |");
    for (const line of spec.table.split("\n")) lines.push(`  ${line}`);
  }
  return lines.join("\n");
}

/** ChartSpec -> 본문에 저장할 차트 펜스(변환 UI가 쓴다). */
export function toFence(spec: ChartSpec): string {
  return `\`\`\`chart\n${toFenceBody(spec)}\n\`\`\``;
}

/** 값 라벨 - 정수는 천 단위 쉼표, 소수는 한 자리(백엔드 _format_value와 같은 규칙).
 *
 * 0이 아닌 값을 "0"이라고 쓰지는 않는다. 반올림만 하던 때는 산출 변화율 △0.02가 라벨
 * "0"으로 찍혔다 - 감소가 있었다는 것이 그 표의 요지인데 그림은 없었다고 말한 셈이다. */
export function formatValue(value: number): string {
  if (Math.abs(value - Math.round(value)) < 0.05 && (Math.round(value) !== 0 || value === 0)) {
    return Math.round(value).toLocaleString("ko-KR");
  }
  let digits = 1;
  while (digits < 4 && Math.abs(value) < 0.5 * 10 ** -digits) digits++;
  return value.toLocaleString("ko-KR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}
