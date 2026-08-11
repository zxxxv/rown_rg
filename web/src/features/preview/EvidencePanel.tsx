import { AlertTriangle, ChevronDown, ChevronRight, ExternalLink, FileSearch } from "lucide-react";
import { useState } from "react";
import { useSectionEvidence } from "@/api/sections";
import type { EvidenceChunk } from "@/api/types";
import { Badge } from "@/components/ui/badge";

// ─── 근거 추적 ───
// 출처 표기만 있으면 "이 자료 어딘가"까지만 알 수 있어, 사람이 원본을 열어 다시 찾아야
// 검증이 된다. 여기서는 모델이 프롬프트로 받은 청크 원문을 그대로 보여준다.
// 함께 보여주는 두 신호가 창작 탐지의 핵심이다:
//   - 실렸는데 인용되지 않은 근거: 모델이 보고도 안 쓴 자료
//   - 근거 표기 없는 주장: 어떤 근거와도 연결되지 않은 문장

interface EvidencePanelProps {
  projectId: string;
  sectionId: string;
}

export function EvidencePanel({ projectId, sectionId }: EvidencePanelProps) {
  const [open, setOpen] = useState(false);
  // 펼칠 때만 부른다 - 청크 원문은 절당 수만 자라 기본 조회에 얹을 무게가 아니다.
  const query = useSectionEvidence(projectId, sectionId, open);
  const data = query.data;
  const cited = data?.items.filter((i) => i.cited) ?? [];
  const unused = data?.items.filter((i) => !i.cited) ?? [];

  return (
    <section className="mt-4 rounded border border-border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-bg-secondary"
        aria-expanded={open}
      >
        {open ? (
          <ChevronDown className="h-4 w-4 text-fg-tertiary" aria-hidden />
        ) : (
          <ChevronRight className="h-4 w-4 text-fg-tertiary" aria-hidden />
        )}
        <FileSearch className="h-4 w-4 text-fg-secondary" aria-hidden />
        <span className="text-sm font-medium text-fg">근거 추적</span>
        <span className="text-xs text-fg-tertiary">
          문장이 어느 원문에서 나왔는지 청크 단위로 대조합니다
        </span>
      </button>

      {open ? (
        <div className="border-t border-border px-3 py-3">
          {query.isLoading ? (
            <p className="text-xs text-fg-tertiary">근거를 불러오는 중…</p>
          ) : query.error || !data ? (
            <p className="text-xs text-fg-danger">근거를 불러오지 못했습니다.</p>
          ) : (
            <div className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center gap-1.5">
                <Badge variant="secondary">인용된 근거 {data.cited_count}건</Badge>
                {data.pool_size > 0 ? (
                  <Badge variant="outline">
                    작성 때 실린 근거 {data.pool_size}건 중 미사용 {data.unused_count}건
                  </Badge>
                ) : null}
                {data.uncited_count > 0 ? (
                  <Badge variant="outline" className="border-fg-warning/40 bg-bg-warning">
                    근거 표기 없는 주장 {data.uncited_count}건
                  </Badge>
                ) : null}
              </div>

              {!data.traceable ? (
                <p className="flex items-start gap-1.5 rounded border border-border bg-bg-secondary px-2.5 py-2 text-xs text-fg-secondary">
                  <AlertTriangle
                    className="mt-0.5 h-3.5 w-3.5 shrink-0 text-fg-warning"
                    aria-hidden
                  />
                  이 절은 근거 기록이 남기 전에 작성돼 인용 번호와 원문의 대응을 확정할 수
                  없습니다. 아래는 이 절이 인용한 근거 목록이며, 다시 작성하면 번호까지
                  대응됩니다.
                </p>
              ) : null}

              {cited.length > 0 ? (
                <ul className="flex flex-col gap-2">
                  {cited.map((item) => (
                    <EvidenceCard key={item.chunk_id} item={item} />
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-fg-tertiary">이 절 본문에 인용 표기가 없습니다.</p>
              )}

              {data.uncited_samples.length > 0 ? (
                <details className="rounded border border-fg-warning/30 bg-bg-warning px-2.5 py-2">
                  <summary className="cursor-pointer text-xs font-medium text-fg">
                    근거 표기 없는 주장 {data.uncited_count}건 보기
                  </summary>
                  <ul className="mt-2 flex flex-col gap-1">
                    {data.uncited_samples.map((s) => (
                      <li key={s} className="text-xs leading-relaxed text-fg-secondary">
                        - {s}
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}

              {unused.length > 0 ? (
                <details className="rounded border border-border px-2.5 py-2">
                  <summary className="cursor-pointer text-xs font-medium text-fg">
                    실렸지만 인용되지 않은 근거 {unused.length}건 보기
                  </summary>
                  <ul className="mt-2 flex flex-col gap-2">
                    {unused.map((item) => (
                      <EvidenceCard key={item.chunk_id} item={item} />
                    ))}
                  </ul>
                </details>
              ) : null}
            </div>
          )}
        </div>
      ) : null}
    </section>
  );
}

function EvidenceCard({ item }: { item: EvidenceChunk }) {
  return (
    <li className="rounded border border-border bg-bg px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        {item.number !== null ? (
          <span className="rounded-sm bg-bg-info px-1.5 font-mono text-[11px] text-fg-info">
            [{item.number}]
          </span>
        ) : null}
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-fg">
          {item.source_title ?? "(제목 없음)"}
        </span>
        {item.url ? (
          <a
            href={item.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex shrink-0 items-center gap-1 text-[11px] text-fg-info hover:underline"
          >
            원본 <ExternalLink className="h-3 w-3" />
          </a>
        ) : null}
      </div>
      {item.header_path.length > 0 ? (
        <p className="mt-0.5 truncate text-[11px] text-fg-tertiary">
          {item.header_path.join(" > ")}
        </p>
      ) : null}
      <p className="mt-1.5 max-h-40 overflow-y-auto whitespace-pre-wrap rounded bg-bg-secondary px-2 py-1.5 text-xs leading-relaxed text-fg-secondary">
        {item.content}
      </p>
    </li>
  );
}
