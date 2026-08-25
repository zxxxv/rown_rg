import {
  autoUpdate,
  flip,
  FloatingPortal,
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
//   - 근거 표기 없는 문장: 늘 약한 점선 밑줄(경고색). 배경색으로 칠하면 개조식 보고서가
//     통째로 노래져서 신호가 죽는다 - 그래서 종전엔 토글로 숨겨 뒀는데, 약한 신호로
//     바꾸면 늘 켜 둘 수 있다.
//   - 근거가 있는 문장: 평소엔 표시 없음, 마우스를 올리면 점선이 드러난다.

/** 호버 카드에 실을 한 줄 요약 - 빠진 것을 사람이 할 일로 말한다(판정 등급이 아니라). */
function missingNote(claim: ClaimAlignment): string | null {
  if (claim.status === "uncited") return "인용 표기가 없는 문장입니다 - 자료 없이 쓰였을 수 있습니다";
  if (!claim.span_text) return "참고한 대목을 특정하지 못했습니다 - 원문에서 직접 확인하세요";
  return null;
}

export function ClaimHoverCard({
  claim,
  chunks,
  children,
}: {
  claim: ClaimAlignment;
  /** 절의 근거 청크 - 인용 번호·chunk_id로 자료 이름을 찾는다(추가 fetch 없음) */
  chunks: EvidenceChunk[];
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
  const note = missingNote(claim);
  const webUrl = textFragmentUrl(source?.url ?? null, claim.span_text);

  return (
    <>
      <span
        ref={refs.setReference}
        {...getReferenceProps()}
        tabIndex={0}
        className={cn(
          "cursor-help border-b border-dashed transition-colors focus:outline-none focus-visible:ring-1 focus-visible:ring-accent",
          uncited
            ? "border-fg-warning/60 hover:border-fg-warning"
            : "border-transparent hover:border-border-strong",
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
              {claim.span_text ? (
                <blockquote className="max-h-48 overflow-y-auto border-l-2 border-border-info pl-2 text-xs leading-relaxed text-fg-secondary">
                  {claim.span_text}
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
