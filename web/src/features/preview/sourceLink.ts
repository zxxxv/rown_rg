// ─── 원문 대목으로 바로 가는 링크 ───
// 브라우저 텍스트 프래그먼트(#:~:text=)를 붙이면 크롬·엣지가 그 문장까지 스크롤하고
// 하이라이트한다. 우리는 이미 근거 대목 원문을 갖고 있으니 링크만 만들면 된다.
//
// 이건 "이 자료가 실제로 그렇게 적고 있는가"를 확인하는 보조 경로다. 창작 여부 판정은
// 화면 안의 청크 원문으로 한다 - 원문은 그 사이 갱신됐을 수 있고, 파서가 뭉갠 표처럼
// 모델이 받은 것과 다를 수 있다. 둘은 목적이 다르다.
//
// 실패해도 손해가 없다: 프래그먼트를 못 찾으면 브라우저는 그냥 페이지 상단을 연다.

/** 프래그먼트 문법에서 뜻을 갖는 문자(-, ,, &)까지 인코딩한다. */
function encodeFragment(text: string): string {
  return encodeURIComponent(text).replace(/-/g, "%2D").replace(/,/g, "%2C").replace(/&/g, "%26");
}

/** 마크다운 장식·글머리를 걷어내고 한 줄로 만든다 - 원문 HTML에는 그런 기호가 없다. */
function plainText(snippet: string): string {
  return snippet
    .replace(/^\s*[-*•‣◦○□▪ㅇㅁ]\s*/, "")
    .replace(/[*#>|`~]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** 정확 일치라 짧을수록 잘 걸린다 - 앞부분만 쓰되 단어 중간에서 자르지 않는다. */
const MAX_FRAGMENT_CHARS = 70;
const MIN_FRAGMENT_CHARS = 12;

export function textFragmentUrl(url: string | null, snippet: string | null): string | null {
  if (!url || !snippet) return null;
  if (!/^https?:\/\//i.test(url)) return null; // 업로드 파일 등은 대상이 아니다
  const clean = plainText(snippet);
  if (clean.length < MIN_FRAGMENT_CHARS) return null;
  const head =
    clean.length <= MAX_FRAGMENT_CHARS
      ? clean
      : clean.slice(0, MAX_FRAGMENT_CHARS).replace(/\s+\S*$/, "");
  if (head.length < MIN_FRAGMENT_CHARS) return null;
  return `${url.split("#")[0]}#:~:text=${encodeFragment(head)}`;
}
