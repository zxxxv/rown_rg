import { ExternalLink } from "lucide-react";
import { useEffect, useRef } from "react";
import { useSourceDocument } from "@/api/sections";
import { cn } from "@/lib/utils";

// ─── 원문 뷰어 ───
// 근거 카드가 보여주는 것은 모델이 받은 청크 하나다. 그 대목이 문서 전체 흐름의
// 어디에 있는지(앞뒤 문맥, 어느 소제목 아래인지)는 원문을 열어야 보인다. 색인
// 청크를 원문 순서로 이어 붙여 문서로 보여주고, 지목된 청크(와 그 안의 대목)로
// 스크롤해 강조한다. 마크다운 렌더는 일부러 하지 않는다 - 근거 대조는 "원문
// 그대로"가 원칙이고, 렌더가 글자를 재배치하면 오프셋 강조가 어긋난다.

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

  let prevHeader = "";
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 rounded border border-border bg-bg px-2.5 py-2">
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-fg">
          {doc.title ?? "(제목 없음)"}
        </span>
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
          const focused = c.chunk_id === location.chunkId;
          return (
            <div
              key={c.chunk_id}
              data-chunk={c.chunk_id}
              className={cn("rounded px-2 py-1", focused && "bg-bg-info ring-1 ring-accent/40")}
            >
              {showHeader ? (
                <p className="mb-0.5 mt-2 truncate text-[11px] font-medium text-fg-tertiary">
                  {header}
                </p>
              ) : null}
              <p className="whitespace-pre-wrap text-xs leading-relaxed text-fg-secondary">
                <ChunkBody
                  content={c.content}
                  focused={focused}
                  start={location.start}
                  end={location.end}
                />
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
