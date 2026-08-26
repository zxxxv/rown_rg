import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

// ─── 근거 원문 렌더 ───
// 보고서 본문(MarkdownContent)과는 다른 물건이다. 여기 오는 글은 우리가 쓴 문장이
// 아니라 남의 문서에서 파싱한 원문이라, 인용 마커 제거·표→차트 변환 같은 보고서 전용
// 가공을 하면 원문을 왜곡한다. 표·제목·목록만 사람이 읽을 수 있게 세운다.
//
// 강조는 **파싱 전에** 원문 문자 오프셋 자리에 표식 문자를 끼워 넣고, 파싱이 끝난
// 트리에서 그 표식을 <mark>로 바꾸는 방식이다. 렌더된 글자를 다시 찾아 맞추는 방법은
// 마크다운 기호가 사라진 뒤라 어긋나지만, 이 방법은 오프셋이 원문 기준 그대로다.

const HL_START = ""; // 사용자 영역 문자 - 원문에 나올 일이 없다
const HL_END = "";

/** 강조할 대목에 표식을 끼운다. 오프셋이 어긋나면 원문을 그대로 돌려준다. */
export function markSpan(content: string, start?: number | null, end?: number | null): string {
  const valid = start != null && end != null && start >= 0 && start < end && end <= content.length;
  if (!valid) return content;
  return (
    content.slice(0, start) + HL_START + content.slice(start, end) + HL_END + content.slice(end)
  );
}

type HastNode = {
  type: string;
  value?: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
};

/** 표식 사이의 글자를 <mark>로 감싼다 - 표식이 문단·표 칸을 넘나들어도 상태로 이어 간다. */
function splitOnMarks(value: string, inside: boolean): { nodes: HastNode[]; inside: boolean } {
  const nodes: HastNode[] = [];
  let buf = "";
  let on = inside;
  const flush = () => {
    if (!buf) return;
    nodes.push(
      on
        ? {
            type: "element",
            tagName: "mark",
            properties: { className: ["rounded-sm", "bg-bg-warning", "px-0.5", "text-fg"] },
            children: [{ type: "text", value: buf }],
          }
        : { type: "text", value: buf },
    );
    buf = "";
  };
  for (const ch of value) {
    if (ch === HL_START || ch === HL_END) {
      flush();
      on = ch === HL_START;
      continue;
    }
    buf += ch;
  }
  flush();
  return { nodes, inside: on };
}

function rehypeMarkSpan() {
  return (tree: HastNode) => {
    let inside = false;
    const walk = (node: HastNode) => {
      if (!node.children) return;
      const next: HastNode[] = [];
      for (const child of node.children) {
        if (child.type === "text" && child.value !== undefined) {
          const result = splitOnMarks(child.value, inside);
          inside = result.inside;
          next.push(...result.nodes);
        } else {
          walk(child);
          next.push(child);
        }
      }
      node.children = next;
    };
    walk(tree);
  };
}

// 원문 패널은 좁다(사이드 패널) - 본문 글자 크기에 맞춰 작게, 표는 가로 스크롤로.
const COMPONENTS: Components = {
  h1: ({ children }) => <p className="mb-1 mt-2 text-xs font-semibold text-fg">{children}</p>,
  h2: ({ children }) => <p className="mb-1 mt-2 text-xs font-semibold text-fg">{children}</p>,
  h3: ({ children }) => <p className="mb-1 mt-2 text-xs font-semibold text-fg">{children}</p>,
  h4: ({ children }) => <p className="mb-1 mt-2 text-xs font-semibold text-fg">{children}</p>,
  h5: ({ children }) => <p className="mb-1 mt-2 text-xs font-semibold text-fg">{children}</p>,
  h6: ({ children }) => <p className="mb-1 mt-2 text-xs font-semibold text-fg">{children}</p>,
  p: ({ children }) => <p className="mb-1.5 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="mb-1.5 list-disc pl-4 last:mb-0">{children}</ul>,
  ol: ({ children }) => <ol className="mb-1.5 list-decimal pl-4 last:mb-0">{children}</ol>,
  li: ({ children }) => <li className="mb-0.5">{children}</li>,
  strong: ({ children }) => <strong className="font-semibold text-fg">{children}</strong>,
  blockquote: ({ children }) => (
    <blockquote className="mb-1.5 border-l-2 border-border pl-2 text-fg-tertiary">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-2 border-border" />,
  code: ({ children }) => (
    <code className="rounded bg-bg-secondary px-1 font-mono text-[11px]">{children}</code>
  ),
  pre: ({ children }) => (
    <pre className="mb-1.5 overflow-x-auto rounded bg-bg-secondary p-2 text-[11px]">{children}</pre>
  ),
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer" className="text-fg-info hover:underline">
      {children}
    </a>
  ),
  // 표는 원문 대조에서 가장 중요한 형식이다 - 파이프 문자로 보이면 숫자를 못 읽는다.
  table: ({ children }) => (
    <div className="mb-1.5 overflow-x-auto">
      <table className="w-full border-collapse text-[11px]">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-border bg-bg-secondary px-1.5 py-1 text-left font-medium text-fg">
      {children}
    </th>
  ),
  td: ({ children }) => <td className="border border-border px-1.5 py-1 align-top">{children}</td>,
};

export function SourceMarkdown({ content, className }: { content: string; className?: string }) {
  return (
    <div className={cn("text-xs leading-relaxed text-fg-secondary", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeMarkSpan]}
        components={COMPONENTS}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
