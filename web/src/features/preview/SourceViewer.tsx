import { ExternalLink, FileText } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { apiUrl } from "@/api/client";
import { useSourceDocument } from "@/api/sections";
import { cn } from "@/lib/utils";
import { markSpan, SourceMarkdown } from "./SourceMarkdown";

// ─── 원문 뷰어 ───
// 근거 카드가 보여주는 것은 모델이 받은 청크 하나다. 그 대목이 문서 전체 흐름의
// 어디에 있는지(앞뒤 문맥, 어느 소제목 아래인지)는 원문을 열어야 보인다. 색인
// 청크를 원문 순서로 이어 붙여 문서로 보여주고, 지목된 청크(와 그 안의 대목)로
// 스크롤해 강조한다. 파싱된 원문은 마크다운이라 날것으로 두면 표가 파이프 문자
// 줄로 보여 숫자를 대조할 수 없다 - 렌더해서 보여준다(2026-08-27 지시).
// 한때 "렌더가 글자를 재배치하면 오프셋 강조가 어긋난다"를 이유로 안 했는데,
// 강조를 **파싱 전에 오프셋 자리에 표식을 끼우는 방식**으로 옮겨 그 문제를 없앴다
// (SourceMarkdown.markSpan). 글자 하나까지 원문과 맞춰 봐야 할 때를 위해
// "원문 그대로" 토글은 남긴다 - 근거 대조가 이 패널의 본업이다.

export type SourceLocation = {
  sourceId: string;
  /** 강조할 청크 - null이면 문서 처음부터 보여준다 */
  chunkId: string | null;
  /** 청크 본문 안 문자 오프셋 - 없으면 청크 전체를 강조 */
  start?: number | null;
  end?: number | null;
};

/** 지목된 대목만 <mark>로 감싼다 - 오프셋이 어긋나 있으면 강조 없이 본문만. */
function ChunkBody({
  content,
  focused,
  start,
  end,
}: {
  content: string;
  focused: boolean;
  start?: number | null;
  end?: number | null;
}) {
  const valid =
    focused && start != null && end != null && start >= 0 && start < end && end <= content.length;
  if (!valid) return <>{content}</>;
  return (
    <>
      {content.slice(0, start as number)}
      <mark className="rounded-sm bg-bg-warning px-0.5 text-fg">
        {content.slice(start as number, end as number)}
      </mark>
      {content.slice(end as number)}
    </>
  );
}

export function SourceViewer({
  projectId,
  location,
}: {
  projectId: string;
  location: SourceLocation;
}) {
  const query = useSourceDocument(projectId, location.sourceId);
  const bodyRef = useRef<HTMLDivElement>(null);
  const [raw, setRaw] = useState(false);

  // 문서가 준비되면 지목 청크를 가운데로 - 렌더 직후라 한 프레임 늦춘다
  useEffect(() => {
    if (!query.data || !location.chunkId) return;
    const raf = requestAnimationFrame(() => {
      bodyRef.current
        ?.querySelector(`[data-chunk="${location.chunkId}"]`)
        ?.scrollIntoView({ block: "center" });
    });
    return () => cancelAnimationFrame(raf);
  }, [query.data, location]);

  if (query.isLoading) {
    return <p className="px-1 py-2 text-xs text-fg-tertiary">원문을 불러오는 중입니다...</p>;
  }
  const doc = query.data;
  if (query.isError || !doc) {
    return <p className="px-1 py-2 text-xs text-fg-danger">원문을 불러오지 못했습니다.</p>;
  }
  if (doc.chunks.length === 0) {
    return (
      <p className="px-1 py-2 text-xs text-fg-secondary">
        색인된 본문이 없습니다 - 자료가 삭제되었거나 본문 없이 등록된 자료입니다.
      </p>
    );
  }

  // 페이지 정보가 있으면 PDF 자료다(페이지는 PDF 파서만 만든다). 지목된 청크의
  // 페이지로 브라우저 PDF 뷰어를 바로 연다 - 쿠키 인증이라 새 탭 링크로 충분하다.
  const focusedPage = doc.chunks.find((c) => c.chunk_id === location.chunkId)?.page ?? null;
  const hasPages = doc.chunks.some((c) => c.page !== null);
  const fileUrl = apiUrl(`projects/${projectId}/sources/${location.sourceId}/file`);
  const pdfHref = (page: number | null) => (page ? `${fileUrl}#page=${page}` : fileUrl);

  let prevHeader = "";
  let prevPage: number | null = null;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 rounded border border-border bg-bg px-2.5 py-2">
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-fg">
          {doc.title ?? "(제목 없음)"}
        </span>
        <button
          type="button"
          onClick={() => setRaw((v) => !v)}
          title={
            raw
              ? "표·제목을 정리해 읽기 좋게 보여줍니다"
              : "마크다운 기호까지 원문 글자 그대로 보여줍니다"
          }
          className="shrink-0 rounded border border-border px-1.5 py-0.5 text-[11px] text-fg-secondary hover:bg-bg-secondary"
        >
          {raw ? "정리해 보기" : "원문 그대로"}
        </button>
        {hasPages ? (
          <a
            href={pdfHref(focusedPage)}
            target="_blank"
            rel="noreferrer"
            title="브라우저 PDF 뷰어로 원본을 해당 페이지에서 엽니다"
            className="inline-flex shrink-0 items-center gap-1 text-[11px] text-fg-info hover:underline"
          >
            PDF 원본{focusedPage ? ` p.${focusedPage}` : ""} 열기
            <FileText className="h-3 w-3" />
          </a>
        ) : null}
        {doc.url ? (
          <a
            href={doc.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex shrink-0 items-center gap-1 text-[11px] text-fg-info hover:underline"
          >
            원본 열기 <ExternalLink className="h-3 w-3" />
          </a>
        ) : null}
      </div>
      <div ref={bodyRef} className="flex flex-col">
        {doc.chunks.map((c) => {
          const header = c.header_path.join(" > ");
          const showHeader = header !== "" && header !== prevHeader;
          if (header) prevHeader = header;
          // 페이지가 바뀌는 지점에만 p.N을 찍는다 - 문서를 훑을 때의 이정표.
          const showPage = c.page !== null && c.page !== prevPage;
          if (c.page !== null) prevPage = c.page;
          const focused = c.chunk_id === location.chunkId;
          return (
            <div
              key={c.chunk_id}
              data-chunk={c.chunk_id}
              className={cn("rounded px-2 py-1", focused && "bg-bg-info ring-1 ring-accent/40")}
            >
              {showPage ? (
                <a
                  href={pdfHref(c.page)}
                  target="_blank"
                  rel="noreferrer"
                  title="PDF 원본을 이 페이지에서 엽니다"
                  className="mt-2 inline-block font-mono text-[10px] text-fg-tertiary hover:text-fg-info hover:underline"
                >
                  p.{c.page}
                </a>
              ) : null}
              {showHeader ? (
                <p className="mb-0.5 mt-2 truncate text-[11px] font-medium text-fg-tertiary">
                  {header}
                </p>
              ) : null}
              {raw ? (
                <p className="whitespace-pre-wrap text-xs leading-relaxed text-fg-secondary">
                  <ChunkBody
                    content={c.content}
                    focused={focused}
                    start={location.start}
                    end={location.end}
                  />
                </p>
              ) : (
                <SourceMarkdown
                  content={focused ? markSpan(c.content, location.start, location.end) : c.content}
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
