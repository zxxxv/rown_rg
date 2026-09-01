// 버전 비교의 텍스트 diff - 의존성 없는 소형 LCS. 한글 보고서 산문에 맞춘 규약:
// - 절 본문은 먼저 블록(빈 줄 기준) 단위로 맞추고, 바뀐 블록 쌍 안에서만 단어
//   단위로 색칠한다. 전체를 단어 diff로 갈면 표·차트 펜스가 뒤섞여 못 읽는다.
// - 표(| 행)·차트 펜스 블록은 단어 색칠 대상이 아니다 - 블록 교체(구/신)로 보여준다.

export type DiffOp = { type: "same" | "add" | "del"; text: string };

export type BlockOp =
  | { type: "same"; text: string }
  | { type: "add"; text: string }
  | { type: "del"; text: string }
  | { type: "change"; before: string; after: string };

// 단어 diff가 감당할 토큰 상한 - 넘으면 통째 교체로 폴백(O(n*m) DP 보호).
const MAX_TOKENS = 1500;

function tokens(text: string): string[] {
  // 공백을 토큰으로 보존한다 - 재조립 시 원문 간격이 그대로 남는다.
  return text.split(/(\s+)/).filter((t) => t.length > 0);
}

/** 두 문자열의 단어 단위 diff. 상한 초과 시 [del, add] 통째 교체. */
export function diffWords(a: string, b: string): DiffOp[] {
  if (a === b) return a ? [{ type: "same", text: a }] : [];
  const ta = tokens(a);
  const tb = tokens(b);
  if (ta.length > MAX_TOKENS || tb.length > MAX_TOKENS) {
    return [
      { type: "del", text: a },
      { type: "add", text: b },
    ];
  }
  // LCS 길이 테이블 (뒤에서부터)
  const n = ta.length;
  const m = tb.length;
  const dp: Uint32Array[] = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = ta[i] === tb[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const ops: DiffOp[] = [];
  const push = (type: DiffOp["type"], text: string) => {
    const last = ops[ops.length - 1];
    if (last && last.type === type) last.text += text;
    else ops.push({ type, text });
  };
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (ta[i] === tb[j]) {
      push("same", ta[i]);
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      push("del", ta[i]);
      i++;
    } else {
      push("add", tb[j]);
      j++;
    }
  }
  while (i < n) {
    push("del", ta[i]);
    i++;
  }
  while (j < m) {
    push("add", tb[j]);
    j++;
  }
  return ops;
}

export function splitBlocks(content: string): string[] {
  return content
    .split(/\n{2,}/)
    .map((b) => b.trim())
    .filter((b) => b.length > 0);
}

/** 표·차트 펜스 등 "단어 색칠하면 깨지는" 블록인가. */
export function isOpaqueBlock(block: string): boolean {
  return block.includes("\n|") || block.startsWith("|") || block.startsWith("```");
}

/** 블록 단위 정렬 - 같은 블록은 유지, 인접한 삭제·추가 런은 쌍(change)으로 묶는다. */
export function diffBlocks(before: string, after: string): BlockOp[] {
  const a = splitBlocks(before);
  const b = splitBlocks(after);
  const n = a.length;
  const m = b.length;
  const dp: Uint32Array[] = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const raw: BlockOp[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      raw.push({ type: "same", text: a[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      raw.push({ type: "del", text: a[i] });
      i++;
    } else {
      raw.push({ type: "add", text: b[j] });
      j++;
    }
  }
  while (i < n) raw.push({ type: "del", text: a[i++] });
  while (j < m) raw.push({ type: "add", text: b[j++] });

  // 인접 del 런 + add 런 → change 쌍 (남는 쪽은 그대로 del/add)
  const out: BlockOp[] = [];
  let k = 0;
  while (k < raw.length) {
    if (raw[k].type !== "del") {
      out.push(raw[k]);
      k++;
      continue;
    }
    const dels: string[] = [];
    while (k < raw.length && raw[k].type === "del") {
      dels.push((raw[k] as { text: string }).text);
      k++;
    }
    const adds: string[] = [];
    while (k < raw.length && raw[k].type === "add") {
      adds.push((raw[k] as { text: string }).text);
      k++;
    }
    const pairs = Math.min(dels.length, adds.length);
    for (let p = 0; p < pairs; p++) out.push({ type: "change", before: dels[p], after: adds[p] });
    for (let p = pairs; p < dels.length; p++) out.push({ type: "del", text: dels[p] });
    for (let p = pairs; p < adds.length; p++) out.push({ type: "add", text: adds[p] });
  }
  return out;
}
