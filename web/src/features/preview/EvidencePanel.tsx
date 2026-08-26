import { ExternalLink, FileSearch } from "lucide-react";
import { useSectionEvidence } from "@/api/sections";
import type { ClaimAlignment, EvidenceChunk, SectionEvidence } from "@/api/types";
import { confirmedSpan } from "./ClaimHoverCard";
import { markerNumbers as markerNumbersOf } from "./markers";
import type { SourceLocation } from "./SourceViewer";
import { textFragmentUrl } from "./sourceLink";

// ─── 근거 추적 ───
// 사람이 이 화면에서 알고 싶은 것은 하나다: **이 문장이 어느 자료의 어느 대목을 보고
// 쓰였나.** 그 하나에 답하도록 2026-08-26에 화면을 걷어냈다.
//
// 전에는 판정 등급 5종(대목 특정·추정·못 찾음·외국어·표기 없음)·겹침 퍼센트·수치별
// 위치 버튼·청크 원문 카드·'같은 자료의 다른 대목' 접힘까지 한 패널에 얹혀 있었다.
// 대부분 QA 내부 지표라 읽는 사람에게는 판단할 거리가 아니었고, 청크 카드는 문장이
// 인용한 대목을 이미 보여준 뒤 같은 글을 한 번 더 싣는 중복이었다.
//
// 남긴 것: 문장 → 출처(제목·번호) → 참고한 대목 → 원문에서 보기.
// 빠진 것을 알리는 한 줄(대목 못 찾음·표기 없음·근거 없는 수치)만 덧붙인다 —
// "어디를 참고했나"의 답이 "아무 데도"인 경우도 답이기 때문이다.

/** 문장이 무엇을 못 갖췄는지 한 줄로 — 판정 등급 대신 사람이 할 일로 말한다.
 *
 * weak·unmatched·crosslingual의 차이는 검출기 사정이지 읽는 사람의 사정이 아니다.
 * 셋 다 "대목을 못 집었으니 직접 보라"로 합친다. */
function missingNote(claim: ClaimAlignment): string | null {
  if (claim.status === "uncited") return "인용 표기가 없는 문장입니다";
  if (!confirmedSpan(claim)) return "참고한 대목을 특정하지 못했습니다 - 원문에서 직접 확인하세요";
  return null;
}

function ClaimRow({
  claim,
  chunks,
  onLocate,
}: {
  claim: ClaimAlignment;
  chunks: EvidenceChunk[];
  onLocate?: (loc: SourceLocation) => void;
}) {
  const chunk = claim.chunk_id ? chunks.find((c) => c.chunk_id === claim.chunk_id) : undefined;
  // 대목을 못 집었으면 인용 번호로라도 자료를 찾아 준다 - 자료 이름조차 없으면
  // 사람이 원본을 열 실마리가 사라진다.
  const fallback =
    !chunk && claim.numbers.length > 0
      ? chunks.find((c) => c.number === claim.numbers[0])
      : undefined;
  const source = chunk ?? fallback;
  const note = missingNote(claim);
  const locate =
    onLocate && source?.source_id
      ? () =>
          onLocate({
            sourceId: source.source_id as string,
            chunkId: source.chunk_id,
            start: claim.span_start,
            end: claim.span_end,
          })
      : undefined;
  const webUrl = textFragmentUrl(source?.url ?? null, confirmedSpan(claim));

  return (
    <li className="rounded border border-border bg-bg px-3 py-2.5">
      <p className="text-xs leading-relaxed text-fg">{claim.claim}</p>

      {source ? (
        <p className="mt-2 flex flex-wrap items-baseline gap-x-1.5 gap-y-0.5 text-[11px]">
          {source.number !== null ? (
            <span className="rounded-sm bg-bg-info px-1 font-mono text-fg-info">
              [{source.number}]
            </span>
          ) : null}
          <span className="min-w-0 text-fg-secondary">{source.source_title ?? "(제목 없음)"}</span>
        </p>
      ) : null}

      {confirmedSpan(claim) ? (
        <blockquote className="mt-1.5 border-l-2 border-border-info pl-2 text-xs leading-relaxed text-fg-secondary">
          {confirmedSpan(claim)}
        </blockquote>
      ) : null}

      {note ? <p className="mt-1.5 text-[11px] text-fg-tertiary">{note}</p> : null}

      {claim.ungrounded.length > 0 ? (
        // 근거에서 못 찾은 수치 - "어디를 참고했나"의 답이 '아무 데도'인 경우라 남긴다.
        <p className="mt-1 text-[11px] text-fg-danger">
          원문에서 못 찾은 수치: {claim.ungrounded.join(", ")}
        </p>
      ) : null}

      {locate || webUrl ? (
        <div className="mt-2 flex flex-wrap items-center gap-3">
          {locate ? (
            <button
              type="button"
              onClick={locate}
              className="inline-flex items-center gap-1 text-[11px] text-fg-info hover:underline"
            >
              원문에서 보기 <FileSearch className="h-3 w-3" />
            </button>
          ) : null}
          {webUrl ? (
            <a
              href={webUrl}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-[11px] text-fg-info hover:underline"
            >
              웹 원본 <ExternalLink className="h-3 w-3" />
            </a>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

// ─── 블록 단위 근거 ───
// 절 전체 목록은 본문 맨 아래에 있어 읽는 자리에서 멀다. 블록을 고르면 그 블록이 쓴 근거만
// 우측 패널에 띄운다 - 절에 출처가 12건이어도 한 블록이 쓰는 건 보통 1~3건이라, 좁혀 보여야
// 대조가 실제로 일어난다.

/** 본문 텍스트에 등장하는 인용 번호 집합 - 블록별 근거 유무 판정(표시·패널)의 공통 원천.
 *  문법은 markers.ts가 단일 진실이다(인용 표기 개편 대비). */
export function markerNumbers(text: string): Set<number> {
  return new Set(markerNumbersOf(text));
}

/** 블록 근거 분류 - 배지 카운트(preview)와 패널(BlockEvidence)이 같은 기준을 쓴다.

전역 인용 번호는 자료 단위라, 번호로만 거르면 같은 자료의 다른 블록용 대목(예: 미국
블록에 일본 문단)까지 딸려 온다(2026-08-12 실측: 6.1절 미국 블록 카드 5장 중 실제
근거는 2장). 문장 정렬(claims)이 청크까지 특정한 것을 primary로, 나머지 같은 번호
청크는 related로 가른다. 정렬 정보가 없으면(정렬 실패·구버전) 종전대로 전부 primary.

패널은 이제 문장 줄만 그리므로 primary/related를 렌더에 쓰지 않는다 - 블록 배지의
'근거 N' 카운트가 이 분류를 그대로 쓴다(같은 기준 유지). */
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
  /** 근거 대목을 원문 문서 안에서 보여달라는 요청 - 드로어가 원문 뷰어로 전환한다 */
  onLocate?: (loc: SourceLocation) => void;
}) {
  const query = useSectionEvidence(projectId, sectionId);
  const data = query.data;
  if (!data) return null;

  const { claims } = partitionBlockEvidence(blocks, data);

  if (claims.length === 0) {
    // 근거가 없으면 그 사실만 한 줄로 알린다 - 빈 패널이 본문 폭을 먹으면 손해다.
    return (
      <p className="rounded border border-border bg-bg px-2.5 py-2 text-xs text-fg-secondary">
        이 블록에는 인용 표기가 없습니다 - 근거 없이 쓰였을 수 있습니다.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs font-medium text-fg">문장 {claims.length}개가 참고한 대목</p>
      <ul className="flex flex-col gap-2">
        {claims.map((c) => (
          // 내용으로 키를 만든다 - 같은 문장이 두 번 나와도 인용 대목이 다르면 갈린다.
          <ClaimRow
            key={`${c.claim}|${c.chunk_id ?? ""}|${c.span_start ?? ""}`}
            claim={c}
            chunks={data.items}
            onLocate={onLocate}
          />
        ))}
      </ul>
    </div>
  );
}
