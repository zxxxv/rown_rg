import {
  autoUpdate,
  FloatingPortal,
  flip,
  offset,
  shift,
  useDismiss,
  useFloating,
  useFocus,
  useHover,
  useInteractions,
  useRole,
} from "@floating-ui/react";
import { ExternalLink } from "lucide-react";
import { type ReactNode, useState } from "react";
import type { ClaimAlignment, EvidenceChunk } from "@/api/types";
import { cn } from "@/lib/utils";
import { textFragmentUrl } from "./sourceLink";

// 본문 문장에 마우스를 올리면 "이 문장이 어느 자료의 어느 대목을 보고 쓰였나"를 그 자리에서
// 보여준다. 근거 패널(오른쪽 드로어)과 같은 답을 읽던 자리에서 바로 준다 - 블록을 골라
// 패널을 여는 왕복이 없어야 읽으면서 대조가 일어난다(2026-08-26 사용자 요청).
//
// 표시 규약:
//   - 표기 없는 문장(AI 서술): 주황 점선 밑줄. 배경색으로 칠하면 개조식 보고서가 통째로
//     노래져 신호가 죽으므로 밑줄만 쓴다.
//   - 인용한 문장: 파란 점선 밑줄. 색은 본문 [n] 배지와 같은 계열이라 "번호가 붙은
//     문장"과 눈으로 이어진다.
//   - 둘 다 화면에서 끄고 켤 수 있다(preview 상단 스위치). 꺼도 마우스를 올리면 점선이
//     드러나고 카드가 뜬다 - 표시는 훑기용, 호버는 확인용.
//
// 밑줄은 border-b가 아니라 text-decoration으로 그린다 - 1px 테두리는 축소된 화면에서
// 사실상 사라졌다(2026-08-26 실사용 지적). 글자를 따라 흐르고 두께를 줄 수 있다.

/** 대목을 "참고한 대목"이라 부를 수 있는가 - **status가 aligned일 때만**이다.
 *
 * span은 하한 없는 argmax라 인용 청크가 있으면 **무조건** 채워진다(alignment.py의
 * `if best is None or score > best.score`). span_text가 있다고 보여주면 겹침 0.05짜리
 * 엉뚱한 대목이 "이 문장의 근거"로 확신 있게 뜬다 - 침묵이 승인이 되던 원래 버그가
 * "아무 대목이나 = 검증됨"으로 이름만 바꿔 돌아오는 것이고, **틀린 대목은 "확인 못 함"
 * 보다 나쁘다**(2026-08-26 지적).
 *
 * 임계값(0.30)은 실제로 거른다 - 음성 대조 실측: 실제 짝 6% 통과, 다른 프로젝트 청크로
 * 바꿔 물린 짝 0% 통과. 통과한 것만 근거로 말한다.
 */
export function confirmedSpan(claim: ClaimAlignment): string | null {
  return claim.status === "aligned" && claim.span_text ? claim.span_text : null;
}

/** 호버 카드에 실을 한 줄 요약 - 빠진 것을 사람이 할 일로 말한다(판정 등급이 아니라). */
function missingNote(claim: ClaimAlignment): string | null {
  if (claim.status === "uncited")
    return "인용 표기가 없는 문장입니다 - 자료 없이 쓰였을 수 있습니다";
  // 근거가 외국어면 겹침 점수가 성립하지 않는다 - 수치·영문 고유명사만 남아 그것만
  // 맞아도 1.00이 나오고, 그렇게 '일치'가 된 문장의 40%가 실제로는 근거 없음이었다
  // (alignment.overlap_score 주석, 2026-08-12 실측). "대목이 없다"와 "잴 수가 없다"는
  // 사람이 할 일이 다르므로 갈라 말한다 - 실측상 못 맞춘 문장의 70%가 이쪽이다.
  if (claim.status === "crosslingual")
    return "근거가 외국어라 자동 대조가 되지 않습니다 - 원문에서 직접 확인하세요";
  if (!confirmedSpan(claim)) return "참고한 대목을 특정하지 못했습니다 - 원문에서 직접 확인하세요";
  return null;
}

export function ClaimHoverCard({
  claim,
  chunks,
  markCited = false,
  markUncited = true,
  traceable = true,
  children,
}: {
  claim: ClaimAlignment;
  /** 절의 근거 청크 - 인용 번호·chunk_id로 자료 이름을 찾는다(추가 fetch 없음) */
  chunks: EvidenceChunk[];
  /** 인용한 문장에 파란 밑줄 */
  markCited?: boolean;
  /** 표기 없는 문장(AI 서술)에 주황 밑줄 */
  markUncited?: boolean;
  /** 마커를 청크까지 되짚을 수 있는 절인가 - false면 대목을 애초에 못 찾는다 */
  traceable?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const { refs, floatingStyles, context } = useFloating({
    open,
    onOpenChange: setOpen,
    placement: "top",
    middleware: [offset(8), flip(), shift({ padding: 8 })],
    whileElementsMounted: autoUpdate,
  });
  const hover = useHover(context, { delay: { open: 250, close: 120 }, move: false });
  const focus = useFocus(context);
  const dismiss = useDismiss(context);
  const role = useRole(context, { role: "tooltip" });
  const { getReferenceProps, getFloatingProps } = useInteractions([hover, focus, dismiss, role]);

  const uncited = claim.status === "uncited";
  const chunk = claim.chunk_id ? chunks.find((c) => c.chunk_id === claim.chunk_id) : undefined;
  // 대목을 못 집었으면 인용 번호로라도 자료를 찾아 준다 - 자료 이름조차 없으면 실마리가 없다.
  const fallback =
    !chunk && claim.numbers.length > 0
      ? chunks.find((c) => c.number === claim.numbers[0])
      : undefined;
  const source = chunk ?? fallback;
  // 근거 기록(2026-08-11) 이전 작성분은 마커→청크 매핑이 없어 대목을 원리적으로 못 찾는다.
  // 그 보고서는 파랑 전부가 같은 문구라, 배너를 못 보고 6장부터 읽는 사람에게도 알린다.
  const note =
    !traceable && claim.status !== "uncited"
      ? "근거 기록 이전에 작성된 절입니다 - 대목까지는 되짚을 수 없고 자료 단위까지만 확인됩니다"
      : missingNote(claim);
  const webUrl = textFragmentUrl(source?.url ?? null, confirmedSpan(claim));

  return (
    <>
      <span
        ref={refs.setReference}
        {...getReferenceProps()}
        // biome-ignore lint/a11y/noNoninteractiveTabindex: 툴팁 트리거라 키보드로도 근거를 열 수 있어야 한다 - 마우스 전용이면 검증 경로가 포인터 사용자에게만 열린다
        tabIndex={0}
        className={cn(
          "cursor-help underline decoration-dotted decoration-2 underline-offset-4 focus:outline-none focus-visible:ring-1 focus-visible:ring-accent",
          uncited
            ? markUncited
              ? "decoration-fg-warning"
              : "decoration-transparent hover:decoration-fg-warning/70"
            : markCited
              ? "decoration-fg-info"
              : "decoration-transparent hover:decoration-fg-info/70",
        )}
      >
        {children}
      </span>
      {open ? (
        <FloatingPortal>
          <div
            ref={refs.setFloating}
            style={floatingStyles}
            {...getFloatingProps()}
            className="z-50 w-80 max-w-[90vw] rounded-lg border border-border bg-bg p-3 text-left shadow-lg"
          >
            <div className="flex flex-col gap-1.5">
              {source ? (
                <p className="flex flex-wrap items-baseline gap-x-1.5 text-[11px]">
                  {source.number !== null ? (
                    <span className="rounded-sm bg-bg-info px-1 font-mono text-fg-info">
                      [{source.number}]
                    </span>
                  ) : null}
                  <span className="min-w-0 font-medium text-fg">
                    {source.source_title ?? "(제목 없음)"}
                  </span>
                </p>
              ) : null}
              {confirmedSpan(claim) ? (
                <blockquote className="max-h-48 overflow-y-auto border-l-2 border-border-info pl-2 text-xs leading-relaxed text-fg-secondary">
                  {confirmedSpan(claim)}
                </blockquote>
              ) : null}
              {note ? <p className="text-[11px] text-fg-tertiary">{note}</p> : null}
              {claim.ungrounded.length > 0 ? (
                <p className="text-[11px] text-fg-danger">
                  원문에서 못 찾은 수치: {claim.ungrounded.join(", ")}
                </p>
              ) : null}
              {webUrl ? (
                <a
                  href={webUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 self-end text-[11px] text-fg-info hover:underline"
                >
                  웹 원본 <ExternalLink className="h-3 w-3" />
                </a>
              ) : null}
            </div>
          </div>
        </FloatingPortal>
      ) : null}
    </>
  );
}
