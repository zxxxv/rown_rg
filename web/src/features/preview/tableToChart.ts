// 본문 블록의 마크다운 표 -> 차트 스펙. 표를 그래프로 바꾸는 UI가 쓰는 변환 규칙이다.
//
// 자동으로 바꾸지 않는다 - 사람이 표를 보고 고른다. "이건 추세니 꺾은선", "이건 구성비니
// 원형"은 데이터 모양만 봐서는 못 정하고, 표 수치는 이미 근거에 매여 게이트를 통과한 값이라
// 값을 새로 만들 일도 없다. 여기서는 사람이 고르기 좋은 기본값만 세운다.

import { type ChartSpec, type ChartType, MAX_SERIES, tryNumber } from "./chartSpec";

export interface MarkdownTable {
  /** 표 위의 제목 줄 - 번호를 뗀 문구. 없으면 빈 문자열. */
  caption: string;
  headers: string[];
  rows: string[][];
  /** 블록에 붙어 있던 (출처 n) 번호 - 표를 그래프로 바꿔도 근거는 따라간다. */
  source: number[];
}

// 표 제목 줄 - "표: 제목", "[표 2-1] 제목" 둘 다 받는다(MarkdownContent와 같은 규약).
const CAPTION_RE = /^(?:\[\s*표[^\]]*\]|표\s*[\d-]*\s*[:：])\s*(.+)$/;
const SOURCE_MARK_RE = /\(출처\s*(\d{1,3}(?:\s*,\s*\d{1,3})*)\s*\)/g;
const SEPARATOR_RE = /^\|?[\s:|-]+\|?$/;
// 머리글 끝의 "(억 원)" 같은 괄호 - 계열 이름에서 떼어 단위 기본값으로 올린다.
const TRAILING_PAREN_RE = /[（(]\s*(?:단위\s*[:：]\s*)?([^）)]+)\s*[）)]\s*$/;

/** 이 블록이 표 제목 줄 하나인가 - 표 앞 문단으로 따로 사는 "표: 제목"을 알아본다. */
export function isTableCaption(block: string): boolean {
  const text = block.replace(SOURCE_MARK_RE, "").trim();
  return !text.includes("\n") && CAPTION_RE.test(text);
}

function cells(line: string): string[] {
  return line
    .trim()
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((c) => c.replace(/\*\*/g, "").trim());
}

/** 블록 안의 마크다운 표를 읽는다. 표가 없거나 데이터 행이 없으면 null. */
export function findTable(block: string): MarkdownTable | null {
  if (block.includes("```")) return null; // 펜스 안(이미 차트)은 대상이 아니다
  const lines = block.split("\n");
  const start = lines.findIndex((l) => /^\s*\|/.test(l));
  if (start < 0) return null;

  const table: string[][] = [];
  for (let i = start; i < lines.length && /^\s*\|/.test(lines[i]); i++) {
    if (SEPARATOR_RE.test(lines[i].trim())) continue;
    table.push(cells(lines[i]));
  }
  if (table.length < 3) return null; // 머리행 + 항목 2개 미만이면 그릴 게 없다

  const before = lines.slice(0, start).join(" ").trim();
  const captionMatch = CAPTION_RE.exec(before.replace(SOURCE_MARK_RE, "").trim());
  const source: number[] = [];
  for (const m of block.matchAll(SOURCE_MARK_RE)) {
    for (const part of m[1].split(",")) {
      const n = Number(part.trim());
      if (!source.includes(n)) source.push(n);
    }
  }

  const [headers, ...rows] = table;
  // 머리행보다 짧은 행은 빈 칸으로 채운다 - 열 인덱스가 어긋나면 엉뚱한 값을 그린다.
  const padded = rows.map((r) => headers.map((_, i) => r[i] ?? ""));
  return { caption: captionMatch ? captionMatch[1].trim() : "", headers, rows: padded, source };
}

/** 값 열로 쓸 수 있는 열 번호 - 데이터 칸이 **전부** 숫자로 읽히는 열.
 *
 * 과반이 아니라 전부여야 한다. 한 칸이라도 숫자가 아니면 그 계열은 NaN이 되어 어차피
 * 못 그린다. 과반 기준일 때 "의미" 같은 서술 열이(칸마다 연도·%가 섞여 있어서) 수치 열로
 * 뽑혀 기본 선택에 들어갔고, 사람은 왜 변환이 막히는지 모른 채 오류만 봤다. */
export function numericColumns(table: MarkdownTable): number[] {
  return table.headers
    .map((_, col) => col)
    .filter((col) => table.rows.every((r) => tryNumber(r[col]) !== null));
}

/** 머리글에서 단위 후보를 뗀다 - "투자액(억 달러)" -> ["투자액", "억 달러"]. */
export function splitUnit(header: string): [string, string] {
  const m = TRAILING_PAREN_RE.exec(header);
  if (m === null) return [header.trim(), ""];
  return [header.slice(0, m.index).trim() || header.trim(), m[1].trim()];
}

export interface ConvertChoice {
  type: ChartType;
  /** x축으로 쓸 열 번호 */
  xCol: number;
  /** 값 열 번호들 - 원형은 하나만 */
  seriesCols: number[];
  title: string;
  unit: string;
}

/** 표에서 처음 열어 보일 기본 선택 - x축은 첫 비수치 열, 값은 **단위가 같은** 수치 열들.
 *
 * 수치 열을 전부 고르지 않는 이유: "투자액(억 달러)"와 "기업 수"를 한 축에 얹으면
 * 작은 쪽이 바닥에 눌려 안 보인다. 단위가 다른 열은 사람이 일부러 고르게 둔다. */
export function defaultChoice(table: MarkdownTable): ConvertChoice {
  const numeric = numericColumns(table);
  // 비수치 열이 없으면(첫 칸이 "2023년 …"처럼 숫자를 품은 표) 첫 열을 x축으로 쓴다.
  // x축을 **먼저 확정한** 뒤 값 후보에서 빼야 한다 - -1로 걸러 내면 x축 열이 값 계열에도
  // 남아, 화면에는 체크박스도 안 보이는 채로 "숫자가 아닙니다"라며 변환이 막힌다.
  const firstNonNumeric = table.headers.findIndex((_, i) => !numeric.includes(i));
  const xCol = firstNonNumeric < 0 ? 0 : firstNonNumeric;
  const candidates = numeric.filter((c) => c !== xCol);
  const unit = candidates.length > 0 ? splitUnit(table.headers[candidates[0]])[1] : "";
  return {
    type: "bar",
    xCol,
    seriesCols: candidates
      .filter((c) => splitUnit(table.headers[c])[1] === unit)
      .slice(0, MAX_SERIES),
    title: table.caption,
    unit,
  };
}

/** 고른 값 열들의 단위가 섞여 있는가 - 한 축에 얹으면 작은 값이 눌린다는 경고용. */
export function hasMixedUnits(table: MarkdownTable, seriesCols: number[]): boolean {
  const units = seriesCols.map((c) => splitUnit(table.headers[c])[1]);
  return units.length > 1 && units.some((u) => u !== units[0]);
}

// 칸 안의 숫자를 전부 센다. 하나를 넘으면 첫 숫자만 집는 우리 규칙이 값을 잘라 먹는다.
//
// 기호(~ → /)만 보고 판단하면 안 된다. 한글 수 단위가 숫자를 토막 내기 때문이다:
// "1조 96억 원"은 1과 96으로 읽혀 1이 되고, 그러면 1조가 "4,027억 원"(4027)보다
// 작게 그려진다. "3만 6,000명(10년)"도 3이 된다. 구간("643~750")·변화("43.8 → 508")도
// 같은 방식으로 함께 걸린다. 천 단위 콤마는 숫자 하나로 센다("4,027억" = 1개).
const NUMBERS_IN_CELL_RE = /-?\d[\d,]*(?:\.\d+)?/g;

/** 값이 하나로 안 읽히는 칸을 찾는다 - 없으면 null, 있으면 사람이 읽을 설명. */
export function ambiguousCell(
  table: MarkdownTable,
  choice: Pick<ConvertChoice, "xCol" | "seriesCols">,
): string | null {
  for (const row of table.rows) {
    for (const col of choice.seriesCols) {
      const cell = row[col]?.trim() ?? "";
      const found = cell.match(NUMBERS_IN_CELL_RE) ?? [];
      if (found.length > 1) {
        return `'${row[choice.xCol]}' 행의 "${cell}"에서 ${tryNumber(cell)}만 씁니다 - 한 칸에 숫자가 여럿이라 값이 잘립니다`;
      }
    }
  }
  return null;
}

/** 표 + 선택 -> 차트 스펙. 원본 블록은 table에 담아 되돌리기·폴백에 쓴다. */
export function buildSpec(
  table: MarkdownTable,
  choice: ConvertChoice,
  originalBlock: string,
): ChartSpec {
  // x축 이름이 빈 행은 통째로 뺀다 - x와 값을 따로 걸러 내면 개수가 어긋난다.
  const rows = table.rows.filter((r) => r[choice.xCol].trim() !== "");
  const x = rows.map((r) => r[choice.xCol].trim());
  const series = choice.seriesCols.map((col) => ({
    name: splitUnit(table.headers[col])[0] || `계열 ${col + 1}`,
    // 숫자로 안 읽히는 칸은 NaN으로 남긴다 - 0으로 메우면 없는 값이 있는 값처럼 그려진다.
    values: rows.map((r) => tryNumber(r[col]) ?? Number.NaN),
  }));
  return {
    type: choice.type,
    title: choice.title.trim(),
    unit: choice.unit.trim(),
    x,
    series,
    source: table.source,
    table: originalBlock.trim(),
  };
}
