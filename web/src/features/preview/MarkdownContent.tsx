import { Children, cloneElement, isValidElement, type ReactElement, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { SourceHoverCard } from "./SourceHoverCard";

const REF_PATTERN = /\[ref:([a-z0-9_-]+)\]/gi;

function processString(text: string): ReactNode {
  if (!REF_PATTERN.test(text)) return text;
  REF_PATTERN.lastIndex = 0;
  const out: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null = REF_PATTERN.exec(text);
  let key = 0;
  while (match !== null) {
    if (match.index > lastIndex) out.push(text.slice(lastIndex, match.index));
    out.push(<SourceHoverCard key={`ref-${key++}`} srcId={match[1] ?? ""} />);
    lastIndex = match.index + match[0].length;
    match = REF_PATTERN.exec(text);
  }
  if (lastIndex < text.length) out.push(text.slice(lastIndex));
  return <>{out}</>;
}

function processChildren(children: ReactNode): ReactNode {
  return Children.map(children, (child) => {
    if (typeof child === "string") return processString(child);
    if (typeof child === "number" || typeof child === "boolean" || child === null) return child;
    if (isValidElement(child)) {
      const el = child as ReactElement<{ children?: ReactNode }>;
      if (el.props?.children !== undefined) {
        return cloneElement(el, undefined, processChildren(el.props.children));
      }
      return child;
    }
    return child;
  });
}

const components: Components = {
  h1: ({ children, ...props }) => (
    <h1 className="mb-4 mt-6 text-2xl font-semibold text-fg" {...props}>
      {processChildren(children)}
    </h1>
  ),
  h2: ({ children, ...props }) => (
    <h2 className="mb-3 mt-6 border-b border-border pb-2 text-xl font-semibold text-fg" {...props}>
      {processChildren(children)}
    </h2>
  ),
  h3: ({ children, ...props }) => (
    <h3 className="mb-2 mt-5 text-base font-semibold text-fg" {...props}>
      {processChildren(children)}
    </h3>
  ),
  h4: ({ children, ...props }) => (
    <h4 className="mb-2 mt-4 text-sm font-semibold text-fg" {...props}>
      {processChildren(children)}
    </h4>
  ),
  p: ({ children, ...props }) => (
    <p className="my-3 leading-7 text-fg" {...props}>
      {processChildren(children)}
    </p>
  ),
  li: ({ children, ...props }) => (
    <li className="my-1 leading-7 text-fg" {...props}>
      {processChildren(children)}
    </li>
  ),
  ul: ({ children, ...props }) => (
    <ul className="my-3 list-disc space-y-1 pl-6" {...props}>
      {processChildren(children)}
    </ul>
  ),
  ol: ({ children, ...props }) => (
    <ol className="my-3 list-decimal space-y-1 pl-6" {...props}>
      {processChildren(children)}
    </ol>
  ),
  blockquote: ({ children, ...props }) => (
    <blockquote
      className="my-4 border-l-4 border-accent bg-bg-info/40 px-4 py-2 italic text-fg-secondary"
      {...props}
    >
      {processChildren(children)}
    </blockquote>
  ),
  table: ({ children, ...props }) => (
    <table
      className="my-4 w-full border-collapse overflow-hidden rounded border border-border text-sm"
      {...props}
    >
      {processChildren(children)}
    </table>
  ),
  thead: ({ children, ...props }) => (
    <thead className="bg-bg-secondary" {...props}>
      {processChildren(children)}
    </thead>
  ),
  th: ({ children, ...props }) => (
    <th className="border border-border px-3 py-2 text-left font-medium text-fg" {...props}>
      {processChildren(children)}
    </th>
  ),
  td: ({ children, ...props }) => (
    <td className="border border-border px-3 py-2 text-fg-secondary" {...props}>
      {processChildren(children)}
    </td>
  ),
  em: ({ children, ...props }) => (
    <em className="italic" {...props}>
      {processChildren(children)}
    </em>
  ),
  strong: ({ children, ...props }) => (
    <strong className="font-semibold text-fg" {...props}>
      {processChildren(children)}
    </strong>
  ),
  a: ({ children, ...props }) => (
    <a {...props} className="text-fg-info hover:underline">
      {processChildren(children)}
    </a>
  ),
  code: ({ children, ...props }) => (
    <code className="rounded-sm bg-bg-secondary px-1 font-mono text-[13px]" {...props}>
      {processChildren(children)}
    </code>
  ),
  hr: () => <hr className="my-6 border-border" />,
};

export interface MarkdownContentProps {
  content: string;
}

export function MarkdownContent({ content }: MarkdownContentProps) {
  return (
    <div className="prose-doc">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
