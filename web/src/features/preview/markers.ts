// 본문 인용 마커 문법 — **이 파일이 프론트의 단일 진실**이다.
//
// 문법이 두 벌인 상태다: 백엔드 alignment는 로컬 문법(`(출처 n)` / `[n]`)을 쓰고,
// 인용 표기 개편이 아직 머지 대기다. 색칠이 마커 유무로 인용/AI를 가르는 지금,
// 프론트가 한 문법만 하드코딩하면 **개편이 머지되는 순간 파랑이 전부 주황으로 뒤집힌다**
// (2026-08-26 지적). 그래서 ①둘 다 받고 ②패턴을 여기 한 곳에 모은다.
//
// 관대하게 받는 편이 안전하다: 마커를 못 알아보면 "자료를 보고 쓴 글"이 "지어낸 글"로
// 표시되는데, 그 오탐이 반대 방향(놓친 AI 서술)보다 훨씬 해롭다 — 사람이 멀쩡한
// 문장을 지우게 만든다.

/** 직접 인용 `[n]` — 뒤에 `(`가 오면 마크다운 링크라 제외한다. */
export const CITE_MARK_RE = /\[(\d{1,3})\](?!\()/g;

/** 참고 표기 `(출처 3)` `(출처 3, 7)` — 자료·참고 라벨과 전각 괄호도 받는다.
 *  앞 공백까지 함께 집어야 본문에서 걷어낼 때 이중 공백이 남지 않는다.
 *
 *  **걷어내기 전용(엄격)**: 괄호가 번호 뒤에서 바로 닫히는 것만 집는다. 뒤에 말이 더
 *  붙은 변종까지 걷으면 본문 문장이 통째로 지워진다("(출처 39, 편의상 본문에서는 …)"). */
export const SOURCE_MARK_RE =
  /[^\S\n]*[（(]\s*(?:출처|자료|참고)\s*(\d{1,3}(?:\s*,\s*\d{1,3})*)\s*[)）]/g;

/** 판정 전용(관대) — 괄호 안에 말이 더 붙은 실측 변종을 받는다.
 *
 *  완료 보고서 3건 전수 스캔(2026-08-26): "출처·자료·참고"가 든 5,873줄 중 99%는 엄격
 *  패턴으로 잡혔고, 못 잡은 52건에 실제 변종이 섞여 있었다 — "(출처 26 없음)",
 *  "(출처 23, 26 참조)", "(출처 1, 21에 준하는 모형)", "(범위 내 — 출처 35)".
 *  이걸 놓치면 자료를 인용한 문장이 "지어낸 글"(주황)로 뒤집힌다 — 해로운 쪽 오탐이다.
 *
 *  라벨 **바로 뒤에 숫자**가 와야 하므로 산문은 안 걸린다("자료상 미제시",
 *  "2024년 발간 자료 기준", "확보된 자료에 관련 내용이 없어 다루지 못함"). */
export const SOURCE_MARK_LOOSE_RE =
  /[（(][^)）]{0,20}?(?:출처|자료|참고)\s*(\d{1,3}(?:\s*,\s*\d{1,3})*)[^)）]{0,40}?[)）]/g;

/** 괄호 안에 부정어가 있으면 마커가 아니다 — "(출처 26 없음)"은 근거가 **없다고 명시한**
 *  문장이라, 마커로 세면 의미가 정반대로 뒤집혀 파랑(자료를 보고 씀)이 된다.
 *  다른 변종("참조"·"준하는")은 인용 쪽이라 그대로 받는다(실측 2026-08-26). */
const NEGATED_RE = /없음|없다|미제시|불명|미상|해당\s*없/;

function markerMatches(text: string): RegExpMatchArray[] {
  return [...text.matchAll(SOURCE_MARK_LOOSE_RE)].filter((m) => !NEGATED_RE.test(m[0]));
}

/** 두 문법을 합친 판정용 — "이 줄이 근거를 주장하는가". */
export function hasMarker(text: string): boolean {
  CITE_MARK_RE.lastIndex = 0;
  return CITE_MARK_RE.test(text) || markerMatches(text).length > 0;
}

/** 텍스트에 등장하는 인용 번호 — 첫 등장 순서, 중복 없이. 두 문법 모두에서 모은다. */
export function markerNumbers(text: string): number[] {
  const out: number[] = [];
  const push = (raw: string) => {
    for (const token of raw.split(",")) {
      const n = Number.parseInt(token.trim(), 10);
      if (!Number.isNaN(n) && !out.includes(n)) out.push(n);
    }
  };
  for (const m of text.matchAll(CITE_MARK_RE)) push(m[1]);
  // 판정은 관대한 쪽 - 놓치면 인용 문장이 AI 서술로 뒤집힌다. 부정어 괄호는 제외.
  for (const m of markerMatches(text)) push(m[1]);
  return out;
}

/** 참고 표기만 걷어낸 본문 — 렌더 텍스트와 대조하려면 같은 자로 걷어야 한다.
 *  `[n]`은 배지로 남기므로 걷지 않는다(본문에도 그대로 남아 있다). */
export function stripSourceMarks(text: string): string {
  return text.replace(SOURCE_MARK_RE, "");
}

/** 문장의 출처 표기에 번호 하나를 덧붙인다 - 오귀속 교정("출처 n 추가")의 실체.

 *  교체가 아니라 추가다: 문장의 다른 수치는 기존 출처가 받칠 수 있다. 첫 번째
 *  엄격 마커에만 덧붙이고, 이미 그 번호가 있거나 마커가 없으면 null(교정 불가). */
export function appendSourceNumber(sentence: string, n: number): string | null {
  const re = new RegExp(SOURCE_MARK_RE.source); // /g 상태 공유를 피해 새로 만든다
  const m = re.exec(sentence);
  if (!m) return null;
  const numbers = m[1].split(",").map((x) => Number.parseInt(x.trim(), 10));
  if (numbers.includes(n)) return null;
  const closing = m[0].search(/[)）][^)）]*$/);
  const fixed = `${m[0].slice(0, closing)}, ${n}${m[0].slice(closing)}`;
  return sentence.slice(0, m.index) + fixed + sentence.slice(m.index + m[0].length);
}

/** 문장의 출처 표기에서 번호 하나를 뺀다 - appendSourceNumber의 역방향.

 *  잘못 누른 추가를 되돌리고, 후보가 틀렸을 때도 뺄 수 있어야 한다(2026-08-27 지적).
 *  마지막 남은 번호를 빼면 마커째 걷는다 - 빈 "(출처 )"를 남기면 파서가 헛돈다.
 *  그 번호가 없거나 마커가 없으면 null(뺄 것이 없음). */
export function removeSourceNumber(sentence: string, n: number): string | null {
  const re = new RegExp(SOURCE_MARK_RE.source, "g");
  for (const m of sentence.matchAll(re)) {
    const numbers = m[1].split(",").map((x) => Number.parseInt(x.trim(), 10));
    if (!numbers.includes(n)) continue;
    const rest = numbers.filter((x) => x !== n);
    const start = m.index ?? 0;
    if (rest.length === 0) {
      // 마커째 - 앞 공백까지 패턴이 물고 있어 이중 공백이 안 남는다.
      return sentence.slice(0, start) + sentence.slice(start + m[0].length);
    }
    const rebuilt = m[0].replace(m[1], rest.join(", "));
    return sentence.slice(0, start) + rebuilt + sentence.slice(start + m[0].length);
  }
  return null;
}
