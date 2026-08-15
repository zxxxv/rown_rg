import {
  Children,
  cloneElement,
  isValidElement,
  type ReactElement,
  type ReactNode,
  useMemo,
} from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { EvidenceChunk, SectionCitation } from "@/api/types";
import { cn } from "@/lib/utils";
import { ChartBlock } from "./ChartBlock";
import { CitationHoverCard } from "./SourceHoverCard";

// 직접 인용 마커 - 원문을 그대로 옮긴 문장에만 붙는다(백엔드 src/core/citations.py와 동일 규약).
// 문장 안에 그대로 남겨 배지로 렌더한다 - 어느 문장이 원문 그대로인지 보여야 하기 때문.
const CITE_PATTERN = /\[(\d{1,3})\]/g;

// 참고 표기 - 자료를 참고해 다시 쓴 문장에 붙는다. "(출처 3)"과 "(출처 3, 7)" 둘 다 받는다.
// 본문에 흘리지 않고 블록 우상단에 모아 보인다 - 문장마다 번호가 붙으면 읽기를 방해하고,
// 직접 인용과도 구분이 안 됐다(2026-08-11).
const SOURCE_MARK_RE = /[^\S\n]*\(출처\s*(\d{1,3}(?:\s*,\s*\d{1,3})*)\s*\)/g;

function stripSourceMarks(md: string): string {
  // 마커 제거 후 라벨만 남은 줄("- 출처:")은 통째로 버린다 - 빈 껍데기가 표 밑에 뜬다.
  return md
    .replace(SOURCE_MARK_RE, "")
    .split("\n")
    .filter((line) => !BARE_SOURCE_LABEL_RE.test(line))
    .join("\n");
}

/** 본문이 참고한 출처 번호 - 첫 등장 순서, 중복 없이. */
function sourceNumbers(md: string): number[] {
  const out: number[] = [];
  for (const match of md.matchAll(SOURCE_MARK_RE)) {
    for (const part of match[1].split(",")) {
      const n = Number(part.trim());
      if (!out.includes(n)) out.push(n);
    }
  }
  return out;
}

// 표 제목 줄 - 표 바로 위에 오는 "표: 제목". 번호(<표 2-1>)는 장 전체를 알아야 매길 수 있어
// 조립 단계(HWPX)에서만 붙는다 - 프리뷰는 절 단위로 렌더돼 장 번호를 모른다.
const TABLE_CAPTION_RE = /^(?:\[\s*표[^\]]*\]|<\s*표[^>]*>|표\s*[\d-]*\s*[:：])\s*(.+)$/;

// 단위 줄 - 표 제목과 표 사이 "(단위: 백만 원)" 단독 줄(작성 규칙). 우측 정렬 소자로 렌더.
const TABLE_UNIT_RE = /^[（(]\s*단위\s*[:：][^)）]*[)）]$/;

// 표 아래 출처 줄 - "(출처 3)"/"(출처 3, 7)" 단독. 실서지 "출처: 제목"으로 풀어 표 밑에 남긴다
// (HWPX 조립과 같은 규칙 - 캡션 위·출처 아래가 공공 보고서 실측 관례).
// 작성기가 "* 출처: (출처 34, 21)"처럼 라벨·불릿을 붙여 내는 변형도 받는다 - 안 받으면
// 마커만 배지로 빠지고 빈 "출처:" 껍데기가 표 밑에 남는다(2026-08-15 실측, 백엔드와 동일 규칙).
const TABLE_SOURCE_RE =
  /^(?:[※*\-–ㅇ○◦]\s*)?(?:(?:출처|자료|참고)\s*[:：]\s*)?[（(]\s*출처\s*(\d{1,3}(?:\s*,\s*\d{1,3})*)\s*[)）]$/;
// 마커를 걷어낸 뒤 라벨만 남은 껍데기 줄("- 출처:") - 가리키는 것이 없어졌으니 버린다.
const BARE_SOURCE_LABEL_RE = /^[□ㅇ○◦\-*※\s]*(?:출처|자료|참고)\s*[:：]?\s*$/;

// 연도 어깨점 정규화 - ‘24 / `24 / '24 혼용을 오른쪽 홑따옴표(U+2019) 하나로(백엔드와 동일).
const YEAR_QUOTE_RE = /[‘'`´](?=\d{2}(?:년|[.~∼]))/g;

function normalizeYearQuotes(md: string): string {
  return md.replace(YEAR_QUOTE_RE, "’");
}

type CitationMap = Map<number, SectionCitation>;
// 인용 번호 -> 그 번호가 실제로 가리킨 근거 원문. 없으면 호버 카드는 출처 이름만 보여준다.
type EvidenceMap = Map<number, EvidenceChunk>;

function processString(text: string, citations: CitationMap, evidence: EvidenceMap): ReactNode {
  if (citations.size === 0 || !CITE_PATTERN.test(text)) return text;
  CITE_PATTERN.lastIndex = 0;
  const out: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null = CITE_PATTERN.exec(text);
  let key = 0;
  while (match !== null) {
    const number = Number(match[1]);
    const citation = citations.get(number);
    if (citation) {
      if (match.index > lastIndex) out.push(text.slice(lastIndex, match.index));
      out.push(
        <CitationHoverCard
          key={`cite-${key++}`}
          number={number}
          citation={citation}
          evidence={evidence.get(number)}
        />,
      );
      lastIndex = match.index + match[0].length;
    }
    match = CITE_PATTERN.exec(text);
  }
  if (out.length === 0) return text;
  if (lastIndex < text.length) out.push(text.slice(lastIndex));
  return <>{out}</>;
}

function processChildren(
  children: ReactNode,
  citations: CitationMap,
  evidence: EvidenceMap,
): ReactNode {
  return Children.map(children, (child) => {
    if (typeof child === "string") return processString(child, citations, evidence);
    if (typeof child === "number" || typeof child === "boolean" || child === null) return child;
    if (isValidElement(child)) {
      const el = child as ReactElement<{ children?: ReactNode }>;
      if (el.props?.children !== undefined) {
        return cloneElement(el, undefined, processChildren(el.props.children, citations, evidence));
      }
      return child;
    }
    return child;
  });
}

// 모델이 발명한 비표준 대괄호 마커([배경자료 제공됨] 등) 제거 - 백엔드 QA 게이트와 동일 정의.
// 허용: [n]·[그림 …]·[표 …]·마크다운 링크([텍스트](url))는 보존, 그 외 대괄호는 삭제.
const INVENTED_MARKER_RE = / ?\[([^[\]\n]{1,40})\](?!\()/g;
const ALLOWED_BRACKET_RE = /^(?:\d+|그림\s?[-\d. ]+|표\s?[-\d. ]+)$/;

function stripInventedMarkers(md: string): string {
  return md.replace(INVENTED_MARKER_RE, (whole, inner: string) =>
    ALLOWED_BRACKET_RE.test(inner.trim()) ? whole : "",
  );
}

// 개조식 pseudo-마커(□ ㅇ ○ ◦)는 마크다운 문법이 아니라 평범한 문단 텍스트로 렌더된다.
// 문단 선두 글자를 읽어 위계(들여쓰기·간격)를 살린다. '-'/'*'는 실제 마크다운 목록이라 건드리지 않는다.
// 마커 문단은 내어쓰기(첫 줄만 -1.5em 당기고 그만큼 왼쪽 여백)로 렌더해, 두 줄 이상으로
// 넘어가도 둘째 줄이 마커 아래가 아니라 본문 글머리에 붙는다(HWPX 출력과 같은 규칙).
// '-' 목록은 ul에서 마커를 절대배치해 이미 같은 효과가 난다.
const OUTLINE_PARA_RE = /^\s*([□ㅇ○◦])\s/;

function outlineMarker(node: unknown): string | null {
  const el = node as { children?: Array<{ type?: string; value?: string }> } | undefined;
  const first = el?.children?.[0];
  if (first?.type !== "text" || typeof first.value !== "string") return null;
  const m = OUTLINE_PARA_RE.exec(first.value);
  return m ? m[1] : null;
}

/** 문단이 텍스트 한 덩어리면 그 원문을, 아니면 null(링크·강조가 섞이면 제외). */
function plainText(node: unknown): string | null {
  const el = node as { children?: Array<{ type?: string; value?: string }> } | undefined;
  const children = el?.children ?? [];
  if (children.length !== 1) return null;
  const first = children[0];
  if (first?.type !== "text" || typeof first.value !== "string") return null;
  return first.value.trim();
}

/** 문단이 표 제목이면 번호를 뗀 제목 문구를, 아니면 null. */
function tableCaption(node: unknown): string | null {
  const text = plainText(node);
  if (text === null) return null;
  const m = TABLE_CAPTION_RE.exec(text);
  return m ? m[1].trim() : null;
}

// 머리행에서 표 제목을 세울 때 분류축으로 못 쓰는 일반어 - 백엔드 report.py와 같은 정의.
const GENERIC_HEADERS = new Set([
  "구분",
  "항목",
  "분류",
  "내용",
  "비고",
  "번호",
  "순번",
  "값",
  "단계",
  "특징",
]);
const CAPTION_MAX_COLS = 3;

/** 표 머리행으로 제목을 세운다 - 제목 줄이 없는 옛 본문의 폴백(백엔드와 같은 규칙). */
function captionFromHeaders(headers: string[]): string | null {
  const cols = headers.map((h) => h.trim()).filter(Boolean);
  if (cols.length < 2) return null;
  if (GENERIC_HEADERS.has(cols[0])) {
    const rest = cols.slice(1).filter((c) => !GENERIC_HEADERS.has(c));
    return rest.length > 0 ? rest.slice(0, CAPTION_MAX_COLS - 1).join("·") : null;
  }
  return `${cols[0]}별 ${cols.slice(1, CAPTION_MAX_COLS).join("·")}`;
}

function tableCells(line: string): string[] {
  return line
    .trim()
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((c) => c.replace(/\*\*/g, "").trim());
}

// 제목 줄이 없는 표 위에 "표: 제목"을 끼워 넣는다 - 한글 파일에는 머리행으로 세운 제목이
// 붙는데 화면에는 없어서 둘이 어긋났다. 번호([표 2-1])는 장 전체를 알아야 매길 수 있어
// 조립 단계에만 붙는다 - 프리뷰는 절 단위로 렌더돼 장 번호를 모른다.
function addTableCaptions(md: string): string {
  const lines = md.split("\n");
  const out: string[] = [];
  let inFence = false;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*```/.test(line)) inFence = !inFence;
    const startsTable = !inFence && /^\s*\|/.test(line) && !/^\s*\|/.test(lines[i - 1] ?? "");
    if (startsTable) {
      const prev = [...out].reverse().find((l) => l.trim() !== "") ?? "";
      if (!TABLE_CAPTION_RE.test(prev.trim())) {
        const caption = captionFromHeaders(tableCells(line));
        if (caption !== null) {
          if (out.length > 0 && out[out.length - 1].trim() !== "") out.push("");
          out.push(`표: ${caption}`, "");
        }
      }
    }
    out.push(line);
  }
  return out.join("\n");
}

// 표 바로 다음의 (출처 n) 단독 줄을 실서지 "출처: 제목"으로 바꾼다. 여기서 못 푼
// (출처 n)은 이후 stripSourceMarks가 걷어내 우상단 배지로만 남는다.
function resolveTableSources(md: string, citations: CitationMap): string {
  const lines = md.split("\n");
  const out: string[] = [];
  let inFence = false;
  let lastContent = "";
  for (const line of lines) {
    if (/^\s*```/.test(line)) inFence = !inFence;
    const m = !inFence ? TABLE_SOURCE_RE.exec(line.trim()) : null;
    if (m && lastContent.startsWith("|")) {
      const names = m[1]
        .split(",")
        .map((part) => Number(part.trim()))
        .map((n) => citations.get(n)?.title?.trim() || `자료 ${n}`);
      // 표에 딱 붙은 줄이면 빈 줄을 끼워 표를 닫고 나서 쓴다 - 원문은 "* 출처: …"라 목록
      // 시작으로 표가 끊겼지만, 실서지로 푼 결과는 평문이라 빈 줄이 없으면 GFM이 그 줄을
      // 표의 다음 행으로 먹는다(출처가 빈 칸을 달고 표 안에 들어앉았다, 2026-08-15 실측).
      if (out.length > 0 && out[out.length - 1].trim() !== "") out.push("");
      out.push(`출처: ${names.join("; ")}`);
      lastContent = out[out.length - 1];
      continue;
    }
    out.push(line);
    if (line.trim() !== "") lastContent = line.trim();
  }
  return out.join("\n");
}

// 개조식 문단 마커(□ ㅇ 등) 앞에 빈 줄을 보장한다 - 단일 줄바꿈은 마크다운에서 공백으로
// 합쳐져 항목이 한 문단으로 붙어 버리므로, 각 마커 줄을 독립 문단으로 분리한다.
// 코드펜스(```) 안은 건드리지 않는다.
function prepareOutline(md: string): string {
  const lines = md.split("\n");
  const out: string[] = [];
  let inFence = false;
  for (const line of lines) {
    if (/^\s*```/.test(line)) inFence = !inFence;
    if (
      !inFence &&
      OUTLINE_PARA_RE.test(line) &&
      out.length > 0 &&
      out[out.length - 1].trim() !== ""
    ) {
      out.push("");
    }
    out.push(line);
  }
  return out.join("\n");
}

function buildComponents(citations: CitationMap, evidence: EvidenceMap): Components {
  const walk = (children: ReactNode) => processChildren(children, citations, evidence);
  return {
    h1: ({ children, ...props }) => (
      <h1 className="mb-4 mt-6 text-2xl font-semibold text-fg" {...props}>
        {walk(children)}
      </h1>
    ),
    h2: ({ children, ...props }) => (
      <h2
        className="mb-3 mt-6 border-b border-border pb-2 text-xl font-semibold text-fg"
        {...props}
      >
        {walk(children)}
      </h2>
    ),
    h3: ({ children, ...props }) => (
      <h3 className="mb-2 mt-5 text-base font-semibold text-fg" {...props}>
        {walk(children)}
      </h3>
    ),
    h4: ({ children, ...props }) => (
      <h4 className="mb-2 mt-4 text-sm font-semibold text-fg" {...props}>
        {walk(children)}
      </h4>
    ),
    p: ({ children, node, ...props }) => {
      const caption = tableCaption(node);
      if (caption !== null) {
        // 표 제목 - 아래 여백을 줄여 뒤따르는 표에 붙여 놓는다(본문처럼 떠 있지 않게).
        return (
          <p className="mb-1 mt-5 text-sm font-semibold text-fg-secondary" {...props}>
            {caption}
          </p>
        );
      }
      const single = plainText(node);
      if (single !== null && TABLE_UNIT_RE.test(single)) {
        // 단위 줄 - 표 위 우측 정렬 소자(HWPX 렌더와 같은 관례).
        return (
          <p className="mb-1 text-right text-xs text-fg-tertiary" {...props}>
            {single}
          </p>
        );
      }
      if (single !== null && /^출처\s*[:：]/.test(single)) {
        // 표 하단 출처 줄 - 표에 붙여 작은 글자로(캡션 위·출처 아래 관례).
        return (
          <p className="-mt-3 mb-4 text-xs text-fg-tertiary" {...props}>
            {single}
          </p>
        );
      }
      const marker = outlineMarker(node);
      if (marker === "□") {
        // 대주제 - 굵게 + 위 간격으로 논리 묶음의 머리로 도드라지게.
        return (
          <p
            className="mb-2 mt-6 pl-[1.5em] indent-[-1.5em] font-semibold leading-7 text-fg"
            {...props}
          >
            {walk(children)}
          </p>
        );
      }
      if (marker) {
        // ㅇ/○/◦ - 상위 개조식 항목: 들여쓰고 위에 간격을 줘 그룹을 분리한다.
        return (
          <p
            className="mb-1 mt-4 pl-[calc(1rem+1.5em)] indent-[-1.5em] leading-7 text-fg"
            {...props}
          >
            {walk(children)}
          </p>
        );
      }
      return (
        <p className="my-3 leading-7 text-fg" {...props}>
          {walk(children)}
        </p>
      );
    },
    li: ({ children, ...props }) => (
      <li className="my-1 leading-7 text-fg" {...props}>
        {walk(children)}
      </li>
    ),
    // "- 항목"은 개조식 3수준(□ -> ㅇ -> - -> *)이라 ㅇ(pl-4)보다 깊어야 한다.
    // 기본 list-disc는 ㅇ보다 얕게 그려져 위계가 뒤집혀 보였다(2026-08-10 지적).
    // 마커도 원문 그대로 '-'를 쓴다 - 규칙이 정한 기호다.
    ul: ({ children, ...props }) => (
      <ul
        className="my-2 list-none space-y-1 pl-8 [&>li]:relative [&>li]:pl-4 [&>li]:before:absolute [&>li]:before:left-0 [&>li]:before:text-fg-tertiary [&>li]:before:content-['-']"
        {...props}
      >
        {walk(children)}
      </ul>
    ),
    ol: ({ children, ...props }) => (
      <ol className="my-3 list-decimal space-y-1 pl-6" {...props}>
        {walk(children)}
      </ol>
    ),
    blockquote: ({ children, ...props }) => (
      <blockquote
        className="my-4 border-l-4 border-accent bg-bg-info/40 px-4 py-2 italic text-fg-secondary"
        {...props}
      >
        {walk(children)}
      </blockquote>
    ),
    table: ({ children, ...props }) => (
      <table
        className="my-4 w-full border-collapse overflow-hidden rounded border border-border text-sm"
        {...props}
      >
        {walk(children)}
      </table>
    ),
    thead: ({ children, ...props }) => (
      <thead className="bg-bg-secondary" {...props}>
        {walk(children)}
      </thead>
    ),
    th: ({ children, ...props }) => (
      <th className="border border-border px-3 py-2 text-left font-medium text-fg" {...props}>
        {walk(children)}
      </th>
    ),
    td: ({ children, ...props }) => (
      <td className="border border-border px-3 py-2 text-fg-secondary" {...props}>
        {walk(children)}
      </td>
    ),
    em: ({ children, ...props }) => (
      <em className="italic" {...props}>
        {walk(children)}
      </em>
    ),
    strong: ({ children, ...props }) => (
      <strong className="font-semibold text-fg" {...props}>
        {walk(children)}
      </strong>
    ),
    a: ({ children, ...props }) => (
      <a {...props} className="text-fg-info hover:underline">
        {walk(children)}
      </a>
    ),
    code: ({ children, className, ...props }) => {
      // ```chart 펜스는 코드가 아니라 그림이다 - 스펙을 읽어 SVG로 그린다.
      if (className === "language-chart") {
        return <ChartBlock source={String(children)} />;
      }
      return (
        <code
          className={cn("rounded-sm bg-bg-secondary px-1 font-mono text-[13px]", className)}
          {...props}
        >
          {walk(children)}
        </code>
      );
    },
    // 차트 펜스는 <pre>로 감싸지 않는다 - figure가 이미 블록이라 이중 여백이 생긴다.
    pre: ({ children }) => <>{children}</>,
    hr: () => <hr className="my-6 border-border" />,
  };
}

export interface MarkdownContentProps {
  content: string;
  /** 절의 [N] 인용 매핑 - 있으면 본문 마커가 호버 출처 배지로 렌더된다. */
  citations?: SectionCitation[];
  /** 근거 추적 결과 - 있으면 호버 카드에 그 번호가 가리킨 원문 대목이 함께 뜬다. */
  evidence?: EvidenceChunk[];
}

export function MarkdownContent({ content, citations, evidence }: MarkdownContentProps) {
  const citationMap = useMemo<CitationMap>(
    () =>
      new Map(
        (citations ?? [])
          .filter((c): c is SectionCitation & { number: number } => c.number !== null)
          .map((c) => [c.number, c]),
      ),
    [citations],
  );
  const evidenceMap = useMemo<EvidenceMap>(() => {
    const map: EvidenceMap = new Map();
    for (const item of evidence ?? []) {
      // 한 번호에 청크가 여럿이면(같은 자료의 다른 대목) 첫 것만 호버에 띄운다 - 나머지는 근거 패널에서.
      if (item.number !== null && item.cited && !map.has(item.number)) map.set(item.number, item);
    }
    return map;
  }, [evidence]);
  const components = useMemo(
    () => buildComponents(citationMap, evidenceMap),
    [citationMap, evidenceMap],
  );
  // 표 하단 (출처 n)을 먼저 실서지 줄로 풀어야 배지·걷어내기에 휩쓸리지 않는다.
  const resolved = useMemo(
    () => resolveTableSources(stripInventedMarkers(content), citationMap),
    [content, citationMap],
  );
  // 참고 표기는 본문에서 떼어 우상단 배지로 모은다 - 매핑이 있는 번호만(없으면 호버할 게 없다).
  // 표 하단 출처로 풀린 번호는 표 밑에 이미 보이므로 배지에서 자연히 빠진다.
  const badgeNumbers = useMemo(
    () => sourceNumbers(resolved).filter((n) => citationMap.has(n)),
    [resolved, citationMap],
  );
  // 남은 참고 표기를 걷어내고 연도 어깨점을 정규화한 뒤, 개조식 마커(□ ㅇ) 줄을 독립
  // 문단으로 분리해 위계·간격을 살린다. 직접 인용 [n]은 본문에 그대로 남아 배지로 렌더된다.
  const prepared = useMemo(
    () => prepareOutline(addTableCaptions(normalizeYearQuotes(stripSourceMarks(resolved)))),
    [resolved],
  );
  return (
    <div className="prose-doc">
      {badgeNumbers.length > 0 ? (
        // float로 띄워 본문이 배지 아래로 흐르게 한다 - 블록 우상단에 얹힌 모양이 된다.
        <div className="float-right ml-3 flex flex-wrap items-center gap-1">
          {badgeNumbers.map((n) => (
            <CitationHoverCard
              key={`src-${n}`}
              number={n}
              citation={citationMap.get(n) as SectionCitation}
              evidence={evidenceMap.get(n)}
            />
          ))}
        </div>
      ) : null}
      {/* singleTilde 차단 - "1~4조, 2~5명"처럼 물결이 두 번 나오면 사이가 취소선으로 그어진다.
          GFM 원 규격대로 ~~만 취소선으로 받는다(본문 수치 구간은 ~ 하나를 쓴다). */}
      <ReactMarkdown remarkPlugins={[[remarkGfm, { singleTilde: false }]]} components={components}>
        {prepared}
      </ReactMarkdown>
    </div>
  );
}
