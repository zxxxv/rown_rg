import {
  autoUpdate,
  FloatingPortal,
  flip,
  offset,
  shift,
  useDismiss,
  useFloating,
  useHover,
  useInteractions,
} from "@floating-ui/react";
import { ExternalLink, Pencil, RotateCw } from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";
import { cn } from "@/lib/utils";

export type HoverActionKind = "rewrite" | "edit" | "source";

export interface HoverActionsProps {
  onAction: (kind: HoverActionKind) => void;
  children: ReactNode;
  disabled?: boolean;
}

const ACTIONS: { kind: HoverActionKind; icon: typeof RotateCw; label: string }[] = [
  { kind: "rewrite", icon: RotateCw, label: "재작성" },
  { kind: "edit", icon: Pencil, label: "편집" },
  { kind: "source", icon: ExternalLink, label: "출처" },
];

export function HoverActions({ onAction, children, disabled }: HoverActionsProps) {
  const [open, setOpen] = useState(false);
  const { refs, floatingStyles, context } = useFloating({
    open: open && !disabled,
    onOpenChange: setOpen,
    placement: "right-start",
    middleware: [offset(12), flip(), shift({ padding: 8 })],
    whileElementsMounted: autoUpdate,
  });
  const hover = useHover(context, {
    delay: { open: 80, close: 150 },
    enabled: !disabled,
  });
  const dismiss = useDismiss(context);
  const { getReferenceProps, getFloatingProps } = useInteractions([hover, dismiss]);

  return (
    <>
      <div ref={refs.setReference} {...getReferenceProps()} className="relative">
        {children}
      </div>
      {open && !disabled ? (
        <FloatingPortal>
          <div
            ref={refs.setFloating}
            style={floatingStyles}
            {...getFloatingProps()}
            className={cn(
              "z-40 flex flex-col gap-1 rounded-md border border-border bg-bg p-1 shadow-md",
            )}
          >
            {ACTIONS.map((a) => (
              <button
                key={a.kind}
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onAction(a.kind);
                }}
                className="inline-flex items-center gap-2 rounded px-2 py-1 text-xs text-fg-secondary hover:bg-bg-tertiary hover:text-fg"
              >
                <a.icon className="h-3.5 w-3.5" aria-hidden />
                {a.label}
              </button>
            ))}
          </div>
        </FloatingPortal>
      ) : null}
    </>
  );
}
