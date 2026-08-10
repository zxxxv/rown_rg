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
import type { SectionCitation } from "@/api/types";
import { CitationHoverCard } from "./SourceHoverCard";

// 본문 인용 마커 - 백엔드 작성 규약([N], 숫자만)과 동일.
const CITE_PATTERN = /\[(\d{1,3})\]/g;

type CitationMap = Map<number, SectionCitation>;

function processString(text: string, citations: CitationMap): ReactNode {
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
      out.push(<CitationHoverCard key={`cite-${key++}`} number={number} citation={citation} />);
      lastIndex = match.index + match[0].length;
    }
    match = CITE_PATTERN.exec(text);
  }
  if (out.length === 0) return text;
  if (lastIndex < text.length) out.push(text.slice(lastIndex));
  return <>{out}</>;
}

function processChildren(children: ReactNode, citations: CitationMap): ReactNode {
  return Children.map(children, (child) => {
    if (typeof child === "string") return processString(child, citations);
    if (typeof child === "number" || typeof child === "boolean" || child === null) return child;
    if (isValidElement(child)) {
      const el = child as ReactElement<{ children?: ReactNode }>;
      if (el.props?.children !== undefined) {
        return cloneElement(el, undefined, processChildren(el.props.children, citations));
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
const OUTLINE_PARA_RE = /^\s*([□ㅇ○◦])\s/;

function outlineMarker(node: unknown): string | null {
  const el = node as { children?: Array<{ type?: string; value?: string }> } | undefined;
  const first = el?.children?.[0];
  if (first?.type !== "text" || typeof first.value !== "string") return null;
  const m = OUTLINE_PARA_RE.exec(first.value);
  return m ? m[1] : null;
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

function buildComponents(citations: CitationMap): Components {
  const walk = (children: ReactNode) => processChildren(children, citations);
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
      const marker = outlineMarker(node);
      if (marker === "□") {
        // 대주제 - 굵게 + 위 간격으로 논리 묶음의 머리로 도드라지게.
        return (
          <p className="mb-2 mt-6 font-semibold leading-7 text-fg" {...props}>
            {walk(children)}
          </p>
        );
      }
      if (marker) {
        // ㅇ/○/◦ - 상위 개조식 항목: 들여쓰고 위에 간격을 줘 그룹을 분리한다.
        return (
          <p className="mb-1 mt-4 pl-4 leading-7 text-fg" {...props}>
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
    code: ({ children, ...props }) => (
      <code className="rounded-sm bg-bg-secondary px-1 font-mono text-[13px]" {...props}>
        {walk(children)}
      </code>
    ),
    hr: () => <hr className="my-6 border-border" />,
  };
}

export interface MarkdownContentProps {
  content: string;
  /** 절의 [N] 인용 매핑 - 있으면 본문 마커가 호버 출처 배지로 렌더된다. */
  citations?: SectionCitation[];
}

export function MarkdownContent({ content, citations }: MarkdownContentProps) {
  const citationMap = useMemo<CitationMap>(
    () =>
      new Map(
        (citations ?? [])
          .filter((c): c is SectionCitation & { number: number } => c.number !== null)
          .map((c) => [c.number, c]),
      ),
    [citations],
  );
  const components = useMemo(() => buildComponents(citationMap), [citationMap]);
  // 발명 인용 마커 제거 후, 개조식 마커(□ ㅇ) 줄을 독립 문단으로 분리해 위계·간격을 살린다.
  const prepared = useMemo(() => prepareOutline(stripInventedMarkers(content)), [content]);
  return (
    <div className="prose-doc">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {prepared}
      </ReactMarkdown>
    </div>
  );
}
