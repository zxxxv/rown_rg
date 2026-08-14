import { ExternalLink, FileSearch } from "lucide-react";
import { useState } from "react";
import { useSectionEvidence } from "@/api/sections";
import type { ClaimAlignment, EvidenceChunk, SectionEvidence } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import type { SourceLocation } from "./SourceViewer";
import { textFragmentUrl } from "./sourceLink";

// ─── 근거 추적 ───
// 출처 표기만 있으면 "이 자료 어딘가"까지만 알 수 있어, 사람이 원본을 열어 다시 찾아야
// 검증이 된다. 여기서는 모델이 프롬프트로 받은 청크 원문을 그대로 보여준다.
// 함께 보여주는 두 신호가 창작 탐지의 핵심이다:
//   - 실렸는데 인용되지 않은 근거: 모델이 보고도 안 쓴 자료
//   - 근거 표기 없는 주장: 어떤 근거와도 연결되지 않은 문장

const CLAIM_STATUS: Record<string, { label: string; cls: string }> = {
  aligned: { label: "대목 특정", cls: "border-fg-success/40 bg-bg-success" },
  weak: { label: "추정", cls: "border-fg-warning/40 bg-bg-warning" },
  unmatched: { label: "못 찾음", cls: "border-fg-danger/40 bg-bg-danger" },
  uncited: { label: "근거 표기 없음", cls: "border-fg-tertiary/40" },
  // 겹침으로는 판정 불가(한글 주장 + 외국어 근거) - 틀렸다는 뜻이 아니라 사람이 볼 자리
  crosslingual: { label: "외국어 근거", cls: "border-fg-info/40 bg-bg-info" },
};

/** 문장별 대조표 - 기본은 확인이 필요한 문장만 보여준다(대목이 특정된 문장은 볼 이유가 적다). */
function ClaimTable({
  claims,
  chunks,
  onLocate,
}: {
  claims: ClaimAlignment[];
  chunks: EvidenceChunk[];
  onLocate?: (loc: SourceLocation) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const urlOf = new Map(chunks.map((c) => [c.chunk_id, c.url]));
  const srcOf = new Map(chunks.map((c) => [c.chunk_id, c.source_id]));
  // 수치·대목의 "원문에서 보기" - 청크가 속한 자료를 알아야 문서를 연다
  const locate = (chunkId: string | null, start?: number | null, end?: number | null) => {
    if (!onLocate || !chunkId) return undefined;
    const sourceId = srcOf.get(chunkId);
    if (!sourceId) return undefined;
    return () => onLocate({ sourceId, chunkId, start, end });
  };
  const needsCheck = claims.filter((c) => c.status !== "aligned");
  const shown = showAll ? claims : needsCheck;

  return (
    <div className="flex flex-col gap-2 rounded border border-border px-2.5 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-medium text-fg">
          문장별 대조 {shown.length}/{claims.length}
        </p>
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          className="text-[11px] text-fg-info hover:underline"
        >
          {showAll ? "확인 필요한 문장만" : "전체 문장 보기"}
        </button>
      </div>
      {shown.length === 0 ? (
        <p className="text-xs text-fg-tertiary">모든 문장이 원문 대목까지 확인됐습니다.</p>
      ) : (
        <ul className="flex max-h-[32rem] flex-col gap-2 overflow-y-auto">
          {shown.map((c) => {
            const badge = CLAIM_STATUS[c.status] ?? CLAIM_STATUS.uncited;
            return (
              <li key={c.claim} className="rounded border border-border bg-bg px-2.5 py-1.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge variant="outline" className={badge.cls}>
                    {badge.label}
                  </Badge>
                  {c.numbers.map((n) => (
                    <span
                      key={n}
                      className="rounded-sm bg-bg-info px-1 font-mono text-[10px] text-fg-info"
                    >
                      [{n}]
                    </span>
                  ))}
                  {c.status !== "uncited" ? (
                    <span className="font-mono text-[10px] text-fg-tertiary">
                      겹침 {Math.round(c.score * 100)}%
                    </span>
                  ) : null}
                  {c.ungrounded.length > 0 ? (
                    <span className="text-[10px] text-fg-danger">
                      근거 없는 수치 {c.ungrounded.join(", ")}
                    </span>
                  ) : null}
                </div>
                <p className="mt-1 text-xs leading-relaxed text-fg">{c.claim}</p>
                {c.grounded.length > 0 ? (
                  // 근거에서 자리가 확인된 수치 - 누르면 원문 뷰어가 그 줄로 점프한다.
                  // 위치를 가리킬 뿐, 문장 전체가 뒷받침된다는 뜻은 아니다.
                  <div className="mt-1 flex flex-wrap items-center gap-1">
                    <span className="text-[10px] text-fg-tertiary">수치 위치</span>
                    {c.grounded.map((g) => {
                      const go = locate(g.chunk_id, g.start, g.end);
                      return go ? (
                        <button
                          key={`${g.chunk_id}:${g.start}:${g.token}`}
                          type="button"
                          onClick={go}
                          title="근거 원문에서 이 수치가 있는 줄을 봅니다"
                          className="rounded-sm bg-bg-success px-1 font-mono text-[10px] text-fg-success hover:underline"
                        >
                          {g.token}
                        </button>
                      ) : (
                        <span
                          key={`${g.chunk_id}:${g.start}:${g.token}`}
                          className="rounded-sm bg-bg-success px-1 font-mono text-[10px] text-fg-success"
                        >
                          {g.token}
                        </span>
                      );
                    })}
                  </div>
                ) : null}
                {c.span_text ? (
                  <div className="mt-1 border-l-2 border-border-info pl-2">
                    <p className="text-xs leading-relaxed text-fg-secondary">{c.span_text}</p>
                    <div className="flex flex-wrap items-center gap-2">
                      {(() => {
                        const go = locate(c.chunk_id, c.span_start, c.span_end);
                        return go ? (
                          <button
                            type="button"
                            onClick={go}
                            className="mt-0.5 inline-flex items-center gap-1 text-[11px] text-fg-info hover:underline"
                          >
                            이 문장 위치 보기 <FileSearch className="h-3 w-3" />
                          </button>
                        ) : null;
                      })()}
                      {(() => {
                        // 원문 웹페이지로 바로 뛰는 링크(브라우저 텍스트 프래그먼트).
                        const jump = textFragmentUrl(
                          c.chunk_id ? (urlOf.get(c.chunk_id) ?? null) : null,
                          c.span_text,
                        );
                        return jump ? (
                          <a
                            href={jump}
                            target="_blank"
                            rel="noreferrer"
                            className="mt-0.5 inline-flex items-center gap-1 text-[11px] text-fg-info hover:underline"
                          >
                            웹 원본에서 보기 <ExternalLink className="h-3 w-3" />
                          </a>
                        ) : null;
                      })()}
                    </div>
                  </div>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function EvidenceCard({
  item,
  onLocate,
}: {
  item: EvidenceChunk;
  onLocate?: (loc: SourceLocation) => void;
}) {
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
        {/* 외부 '원본' 링크는 뺐다(2026-08-14 사용자 지적: 문장 카드의 링크들과 중복으로
            읽힘). 원문 뷰어 헤더에 '원본 열기'가 있어 경로 손실이 없다. */}
        {onLocate && item.source_id ? (
          <button
            type="button"
            onClick={() =>
              onLocate({
                sourceId: item.source_id as string,
                chunkId: item.chunk_id,
              })
            }
            title="이 대목이 원문 문서의 어디인지 앞뒤 문맥과 함께 봅니다"
            className="inline-flex shrink-0 items-center gap-1 text-[11px] text-fg-info hover:underline"
          >
            문서 전체에서 보기 <FileSearch className="h-3 w-3" />
          </button>
        ) : null}
      </div>
      {item.header_path.length > 0 ? (
        <p className="mt-0.5 truncate text-[11px] text-fg-tertiary">
          {item.header_path.join(" > ")}
        </p>
      ) : null}
      <p className="mt-1.5 max-h-72 overflow-y-auto whitespace-pre-wrap rounded bg-bg-secondary px-2 py-2 text-xs leading-relaxed text-fg-secondary">
        {item.content}
      </p>
    </li>
  );
}

// ─── 블록 단위 근거 ───
// 절 전체 목록은 본문 맨 아래에 있어 읽는 자리에서 멀다. 블록을 고르면 그 블록이 문 근거만
// 우측 패널에 띄운다 - 절에 출처가 12건이어도 한 블록이 쓰는 건 보통 1~3건이라, 좁혀 보여야
// 대조가 실제로 일어난다.

const MARK_RE = /\[(\d+)\]|\(출처\s*([\d,\s]+)\)/g;

/** 본문 텍스트에 등장하는 인용 번호 집합 - 블록별 근거 유무 판정(표시·패널)의 공통 원천. */
export function markerNumbers(text: string): Set<number> {
  const out = new Set<number>();
  for (const m of text.matchAll(MARK_RE)) {
    const raw = m[1] ?? m[2] ?? "";
    for (const token of raw.split(",")) {
      const n = Number.parseInt(token.trim(), 10);
      if (!Number.isNaN(n)) out.add(n);
    }
  }
  return out;
}

/** 블록 근거 분류 - 배지 카운트(preview)와 패널(BlockEvidence)이 같은 기준을 쓴다.

전역 인용 번호는 자료 단위라, 번호로만 거르면 같은 자료의 다른 블록용 대목(예: 미국
블록에 일본 문단)까지 딸려 온다(2026-08-12 실측: 6.1절 미국 블록 카드 5장 중 실제
근거는 2장). 문장 정렬(claims)이 청크까지 특정한 것을 primary로, 나머지 같은 번호
청크는 related로 가른다. 정렬 정보가 없으면(정렬 실패·구버전) 종전대로 전부 primary. */
export function partitionBlockEvidence(
  blocks: string[],
  data: SectionEvidence,
): { primary: EvidenceChunk[]; related: EvidenceChunk[]; claims: ClaimAlignment[] } {
  const numbers = markerNumbers(blocks.join("\n"));
  const items = data.items.filter((i) => i.cited && i.number !== null && numbers.has(i.number));
  // 문장은 본문에서 그대로 잘라낸 것이라 블록 안에 들어 있다 - 번호보다 정확히 좁혀진다.
  const claims = data.claims.filter((c) => blocks.some((b) => b.includes(c.claim)));
  const directIds = new Set(claims.map((c) => c.chunk_id).filter((id): id is string => !!id));
  const primary = items.filter((i) => directIds.has(i.chunk_id));
  if (primary.length === 0) return { primary: items, related: [], claims };
  return { primary, related: items.filter((i) => !directIds.has(i.chunk_id)), claims };
}

export function BlockEvidence({
  projectId,
  sectionId,
  blocks,
  onLocate,
}: {
  projectId: string;
  sectionId: string;
  /** 선택된 블록 본문들 - 여기 등장하는 인용 번호만 추린다 */
  blocks: string[];
  /** 근거 대목·수치를 원문 문서 안에서 보여달라는 요청 - 드로어가 원문 뷰어로 전환한다 */
  onLocate?: (loc: SourceLocation) => void;
}) {
  const query = useSectionEvidence(projectId, sectionId);
  const data = query.data;
  if (!data) return null;

  const { primary, related, claims } = partitionBlockEvidence(blocks, data);
  const flagged = claims.filter((c) => c.status !== "aligned");

  if (primary.length === 0 && related.length === 0 && claims.length === 0) {
    // 근거가 없으면 그 사실만 한 줄로 알린다 - 빈 패널이 본문 폭을 먹으면 손해다.
    return (
      <p className="rounded border border-border bg-bg px-2.5 py-2 text-xs text-fg-secondary">
        이 블록에는 인용 표기가 없습니다 - 근거 없이 쓰였을 수 있습니다.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <p className="text-xs font-medium text-fg">이 블록의 근거 {primary.length}건</p>
        {flagged.length > 0 ? (
          <Badge variant="outline" className="border-fg-warning/40 bg-bg-warning">
            확인 필요 {flagged.length}
          </Badge>
        ) : null}
      </div>
      {/* 전체 목록을 넘긴다 - 걸러낸 목록을 넘기면 표 안의 '전체 문장 보기' 토글이
          같은 목록을 두 번 보여주는 빈 스위치가 된다(2026-08-14 사용자 발견). 기본
          표시는 표 안에서 '확인 필요'만 거른다. */}
      {claims.length > 0 ? (
        <ClaimTable claims={claims} chunks={data.items} onLocate={onLocate} />
      ) : null}
      {primary.length > 0 ? (
        <ul className="flex flex-col gap-2">
          {primary.map((item) => (
            <EvidenceCard key={item.chunk_id} item={item} onLocate={onLocate} />
          ))}
        </ul>
      ) : null}
      {related.length > 0 ? (
        // 같은 출처 번호에 묶여 있지만 이 블록 문장과 정렬되지 않은 대목 - 다른 블록의
        // 근거이거나 잡청크다. 대등하게 나열하면 "엉뚱한 근거"로 읽히므로 접어 둔다.
        <details className="rounded border border-border px-2.5 py-2">
          <summary className="cursor-pointer text-xs font-medium text-fg">
            같은 자료의 다른 대목 {related.length}건 보기
          </summary>
          <p className="mt-1 text-[11px] leading-relaxed text-fg-tertiary">
            이 블록과 같은 출처 번호로 인용됐지만, 이 절의 다른 문장을 뒷받침하는 대목입니다.
          </p>
          <ul className="mt-2 flex flex-col gap-2">
            {related.map((item) => (
              <EvidenceCard key={item.chunk_id} item={item} onLocate={onLocate} />
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}
