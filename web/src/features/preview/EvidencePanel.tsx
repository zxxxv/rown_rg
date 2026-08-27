import { ExternalLink, FileSearch } from "lucide-react";
import { useSectionEvidence } from "@/api/sections";
import { HelpTip } from "@/components/ui/help-tip";
import type { ClaimAlignment, EvidenceChunk, SectionEvidence } from "@/api/types";
import { confirmedSpan } from "./ClaimHoverCard";
import { appendSourceNumber, markerNumbers as markerNumbersOf, removeSourceNumber } from "./markers";
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
 * weak·unmatched의 차이는 검출기 사정이지 읽는 사람의 사정이 아니라 하나로 합친다.
 * 다만 crosslingual은 갈라 말한다 - "대목이 없다"가 아니라 "외국어라 잴 수가 없다"라서
 * 사람이 할 일이 다르고, 실측상 못 맞춘 문장의 70%가 이쪽이다(2026-08-26). */
function missingNote(claim: ClaimAlignment): string | null {
  if (claim.status === "uncited") return "인용 표기가 없는 문장입니다";
  if (claim.status === "crosslingual")
    return "근거가 외국어라 자동 대조가 되지 않습니다 - 원문에서 직접 확인하세요";
  if (!confirmedSpan(claim)) return "참고한 대목을 특정하지 못했습니다 - 원문에서 직접 확인하세요";
  return null;
}

function ClaimRow({
  claim,
  chunks,
  onLocate,
  onFixCitation,
  onRewriteSentence,
  onRemoveCitation,
}: {
  claim: ClaimAlignment;
  chunks: EvidenceChunk[];
  onLocate?: (loc: SourceLocation) => void;
  /** 오귀속 교정 - 이 문장에 출처 번호 하나를 덧붙인다(문장, 추가할 번호) */
  onFixCitation?: (claim: string, number: number) => void;
  /** 주입 의심 조치 - 이 문장을 국소 재작성한다(문장, 문제 수치) */
  onRewriteSentence?: (claim: string, token: string) => void;
  /** 출처 표기 제거 - 잘못 단(또는 잘못 추가한) 번호를 문장에서 뺀다 */
  onRemoveCitation?: (claim: string, number: number) => void;
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
          {claim.numbers.map((n) => {
            // 문장이 단 번호 전부를 배지로 - 추가만 있고 제거가 없으면 잘못 누른
            // 교정을 되돌릴 손잡이가 없다(2026-08-27 지적). ×는 편집 가능일 때만.
            const removable = onRemoveCitation && removeSourceNumber(claim.claim, n) !== null;
            return (
              <span
                key={n}
                className="inline-flex items-center gap-0.5 rounded-sm bg-bg-info px-1 font-mono text-fg-info"
              >
                [{n}]
                {removable ? (
                  <button
                    type="button"
                    title={`이 문장에서 출처 ${n} 표기를 뺍니다`}
                    onClick={() => onRemoveCitation(claim.claim, n)}
                    className="text-fg-tertiary hover:text-fg-danger"
                  >
                    ×
                  </button>
                ) : null}
              </span>
            );
          })}
          <span className="min-w-0 text-fg-secondary">{source.source_title ?? "(제목 없음)"}</span>
        </p>
      ) : null}

      {confirmedSpan(claim) ? (
        <div className="mt-1.5 flex items-start gap-1">
          <blockquote className="min-w-0 flex-1 border-l-2 border-border-info pl-2 text-xs leading-relaxed text-fg-secondary">
            {confirmedSpan(claim)}
          </blockquote>
          <HelpTip title="참고한 대목">
            <p>
              자동 대조가 인용한 자료 안에서 찾아낸, 이 문장을 받치는 원문 대목입니다. 어휘
              겹침 또는 의미 유사도가 확정 기준을 넘은 것만 여기 실립니다.
            </p>
            <p>기준을 못 넘으면 단정하지 않고 "추정 후보"나 확인 안내로 갈립니다.</p>
          </HelpTip>
        </div>
      ) : null}

      {note ? (
        <p className="mt-1.5 flex items-center gap-1 text-[11px] text-fg-tertiary">
          <span className="min-w-0">{note}</span>
          <HelpTip title="이 안내의 뜻">
            <p>
              <b>인용 표기가 없는 문장</b> - AI가 자료 없이 쓴 서술일 수 있어 원문 확인을
              권합니다.
            </p>
            <p>
              <b>근거가 외국어</b> - 한글 문장과 외국어 원문은 글자 겹침으로 잴 수 없어 자동
              확정이 어렵습니다. 틀렸다는 뜻이 아니라 사람이 봐야 한다는 뜻입니다.
            </p>
            <p>
              <b>대목을 특정하지 못함</b> - 인용한 자료까지는 아는데 그 안의 어느 대목인지
              자동으로 못 좁힌 경우입니다.
            </p>
          </HelpTip>
        </p>
      ) : null}

      {claim.candidates.length > 0 ? (
        // 확정은 못 했지만 "여기서 가져왔을 것 같다"를 몇 개 내놓는다 - 대목을 단정하면
        // 거짓 확신이지만 후보로 내놓으면 기계가 좁히고 사람이 고르는 것이 된다.
        // 순위만 쓰므로 문턱이 필요 없고, 순위는 문서 간에 안정적이다(절대 점수는
        // 코퍼스 언어 구성에 따라 크게 흔들린다 - 2026-08-27).
        <details className="mt-2 rounded border border-border-info/50 bg-bg-info/30 px-2 py-1.5">
          <summary className="cursor-pointer text-[11px] text-fg-info">
            추정 후보 {claim.candidates.length}개 - 이 중에 있을 수 있습니다
          </summary>
          <ul className="mt-1.5 flex flex-col gap-1.5">
            {claim.candidates.map((c) => (
              <li key={`${c.chunk_id}:${c.start}`}>
                <button
                  type="button"
                  onClick={() =>
                    onLocate?.({
                      sourceId: chunks.find((x) => x.chunk_id === c.chunk_id)?.source_id ?? "",
                      chunkId: c.chunk_id,
                      start: c.start,
                      end: c.end,
                    })
                  }
                  disabled={!onLocate}
                  className="w-full border-l-2 border-border-info pl-2 text-left text-xs leading-relaxed text-fg-secondary hover:border-fg-info hover:text-fg disabled:cursor-default"
                >
                  {c.text}
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-1.5 text-[10px] text-fg-tertiary">
            자동 추정이라 틀릴 수 있습니다 - 눌러서 원문 위치를 확인하세요.
          </p>
        </details>
      ) : null}

      {claim.ungrounded.length > 0 ? (
        // 근거에서 못 찾은 수치 - "어디를 참고했나"의 답이 '아무 데도'인 경우라 남긴다.
        <p className="mt-1 flex items-center gap-1 text-[11px] text-fg-danger">
          <span>원문에서 못 찾은 수치: {claim.ungrounded.join(", ")}</span>
          <HelpTip title="원문에서 못 찾은 수치">
            <p>
              이 문장이 인용한 자료 안에서 해당 수치를 찾지 못했습니다. 지어냈거나, 다른
              자료에서 왔거나, 자료가 다른 표기(단위 환산 등)로 적었을 수 있습니다.
            </p>
            <p>아래에 "출처 n에 있습니다" 제안이 함께 뜨면 표기만 틀렸을 가능성이 큽니다.</p>
          </HelpTip>
        </p>
      ) : null}

      {(claim.injections ?? []).map((inj) => (
        // 주입 의심 - 연도를 명시한 수치가 (a) 코퍼스 어디에도 없거나(소재 불명)
        // (b) 있긴 한데 그 연도 곁엔 없다(시점 불일치). 조치는 표기 교정이 아니라
        // 문장 자체의 국소 재작성이다 - 수치를 근거의 값으로 바꾸거나 문장을 뺀다.
        <div
          key={`inj-${inj.token}`}
          className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px]"
        >
          <span className="text-fg-danger">
            {inj.located_title
              ? `${inj.token}은(는) "${inj.located_title}"에 있지만 본문이 말한 연도 곁에는 없습니다 - 시점이 다른 값일 수 있습니다`
              : `${inj.token}은(는) 수집한 자료 어디에도 없습니다 - 지어냈거나 옛 지식일 수 있습니다`}
          </span>
          <HelpTip title="연도가 있는 수치의 추가 검사">
            <p>
              문장이 연도를 명시한 수치는 수집한 자료 전체에서 한 번 더 찾아봅니다. 자료
              어디에도 없으면 모델이 지어냈거나 학습 시점의 옛 지식을 썼을 수 있고, 있어도
              그 연도 곁이 아니면 다른 시점의 값을 가져왔을 수 있습니다.
            </p>
            <p>"이 문장 고치기"는 AI에게 근거에 실재하는 값으로 다시 쓰게 합니다.</p>
          </HelpTip>
          {onRewriteSentence ? (
            <button
              type="button"
              onClick={() => onRewriteSentence(claim.claim, inj.token)}
              className="rounded border border-fg-danger/40 px-1.5 py-0.5 text-fg-danger hover:bg-bg-danger"
            >
              이 문장 고치기
            </button>
          ) : null}
        </div>
      ))}

      {(claim.relocations ?? []).map((r) => {
        // 오귀속 제안 - 그 수치가 절의 **다른** 근거에 있다. 자동으로 안 바꾼다:
        // 같은 수치가 우연히 딴 자료에 있을 수 있어, 원문 확인과 적용은 사람 몫이다.
        // 적용은 교체가 아니라 추가다 - 문장의 다른 수치는 기존 출처가 받칠 수 있다.
        const rSource = chunks.find((x) => x.chunk_id === r.chunk_id);
        return (
          <div
            key={`reloc-${r.token}`}
            className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px]"
          >
            <span className="text-fg-secondary">
              {r.token}은(는) <span className="font-mono text-fg-info">[{r.number}]</span>{" "}
              {rSource?.source_title ?? "다른 근거"}에 있습니다
            </span>
            <HelpTip title="출처 표기 교정 제안">
              <p>
                이 수치가 문장이 인용한 자료에는 없지만 이 절의 다른 근거에는 있습니다 -
                내용은 맞는데 출처 번호가 틀렸을 가능성이 큽니다.
              </p>
              <p>
                "원문 확인"으로 실제 자료를 본 뒤 "출처 n 추가"를 누르면 표기에 그 번호가
                덧붙습니다. 잘못 눌렀으면 위 출처 배지의 ×로 뺄 수 있습니다.
              </p>
            </HelpTip>
            {onLocate && rSource?.source_id ? (
              <button
                type="button"
                onClick={() =>
                  onLocate({
                    sourceId: rSource.source_id as string,
                    chunkId: r.chunk_id,
                    start: null,
                    end: null,
                  })
                }
                className="text-fg-info hover:underline"
              >
                원문 확인
              </button>
            ) : null}
            {onFixCitation && appendSourceNumber(claim.claim, r.number) !== null ? (
              <button
                type="button"
                onClick={() => onFixCitation(claim.claim, r.number)}
                className="rounded border border-border-info px-1.5 py-0.5 text-fg-info hover:bg-bg-info"
              >
                출처 {r.number} 추가
              </button>
            ) : null}
          </div>
        );
      })}

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
  onFixCitation,
  onRewriteSentence,
  onRemoveCitation,
}: {
  projectId: string;
  sectionId: string;
  /** 선택된 블록 본문들 - 여기 등장하는 인용 번호만 추린다 */
  blocks: string[];
  /** 근거 대목을 원문 문서 안에서 보여달라는 요청 - 드로어가 원문 뷰어로 전환한다 */
  onLocate?: (loc: SourceLocation) => void;
  /** 오귀속 교정 - 문장에 출처 번호를 덧붙여 저장한다 */
  onFixCitation?: (claim: string, number: number) => void;
  /** 주입 의심 조치 - 문장을 국소 재작성한다 */
  onRewriteSentence?: (claim: string, token: string) => void;
  /** 출처 표기 제거 */
  onRemoveCitation?: (claim: string, number: number) => void;
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
            onFixCitation={onFixCitation}
            onRewriteSentence={onRewriteSentence}
            onRemoveCitation={onRemoveCitation}
          />
        ))}
      </ul>
    </div>
  );
}
