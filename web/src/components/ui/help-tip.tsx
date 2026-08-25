import {
  autoUpdate,
  FloatingFocusManager,
  FloatingPortal,
  flip,
  offset,
  shift,
  useClick,
  useDismiss,
  useFloating,
  useInteractions,
  useRole,
} from "@floating-ui/react";
import { CircleHelp, X } from "lucide-react";
import { type ReactNode, useState } from "react";
import { cn } from "@/lib/utils";

// ─── 도움말 단추 ───
// 긴 설명을 칸 밑에 늘어놓으면 처음 쓰는 사람이 "무엇을 적는 칸인지"를 찾기 전에
// 내부 용어부터 읽게 된다(2026-08-25 전수 조사: 주제 칸 설명이 내부 용어 4문장·170자).
// 화면에는 한 줄만 두고, 자세한 것은 물음표를 눌렀을 때만 뜨게 한다.
//
// 호버가 아니라 **클릭**이다 - 호버 카드는 터치에서 열 수 없고, 읽는 도중 손이 벗어나면
// 닫힌다. 본문을 읽어야 하는 도움말은 눌러서 열고 눌러서 닫는 게 맞다.

export interface HelpTipProps {
  /** 팝업 머리 - 무엇에 대한 도움말인지 */
  title: string;
  /** 자세한 설명 */
  children: ReactNode;
  /** 단추 크기 - 영역 제목 옆이면 "sm", 칸 설명 줄 옆이면 "xs" */
  size?: "xs" | "sm";
  className?: string;
}

export function HelpTip({ title, children, size = "xs", className }: HelpTipProps) {
  const [open, setOpen] = useState(false);
  const { refs, floatingStyles, context } = useFloating({
    open,
    onOpenChange: setOpen,
    placement: "bottom-start",
    middleware: [offset(6), flip(), shift({ padding: 8 })],
    whileElementsMounted: autoUpdate,
  });
  const click = useClick(context);
  const dismiss = useDismiss(context);
  const role = useRole(context, { role: "dialog" });
  const { getReferenceProps, getFloatingProps } = useInteractions([click, dismiss, role]);
  const icon = size === "sm" ? "h-4 w-4" : "h-3.5 w-3.5";

  return (
    <>
      <button
        type="button"
        ref={refs.setReference}
        {...getReferenceProps()}
        aria-label={`${title} 도움말`}
        title={`${title} - 자세히 보기`}
        className={cn(
          "inline-flex shrink-0 items-center justify-center rounded-full p-0.5 align-middle text-fg-tertiary transition-colors hover:bg-bg-secondary hover:text-fg focus:outline-none focus-visible:ring-2 focus-visible:ring-accent",
          open && "bg-bg-info text-accent",
          className,
        )}
      >
        <CircleHelp className={icon} aria-hidden />
      </button>
      {open ? (
        <FloatingPortal>
          <FloatingFocusManager context={context} modal={false}>
            <div
              ref={refs.setFloating}
              style={floatingStyles}
              {...getFloatingProps()}
              // z-50: 대화상자(z-50) 안에서도 열리는 자리가 있다(프롬프트 만들기 등)
              className="z-50 w-[min(22rem,calc(100vw-2rem))] rounded-lg border border-border bg-bg p-3 shadow-lg"
            >
              <div className="mb-1.5 flex items-start justify-between gap-2">
                <p className="text-xs font-semibold text-fg">{title}</p>
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  aria-label="도움말 닫기"
                  className="shrink-0 rounded-sm p-0.5 text-fg-tertiary hover:text-fg"
                >
                  <X className="h-3.5 w-3.5" aria-hidden />
                </button>
              </div>
              <div className="flex flex-col gap-1.5 text-[11px] leading-relaxed text-fg-secondary">
                {children}
              </div>
            </div>
          </FloatingFocusManager>
        </FloatingPortal>
      ) : null}
    </>
  );
}
