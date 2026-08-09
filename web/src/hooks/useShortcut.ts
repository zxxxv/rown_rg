import { useEffect, useRef } from "react";

export interface ShortcutOptions {
  cmd?: boolean;
  shift?: boolean;
  alt?: boolean;
  enabled?: boolean;
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName.toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return true;
  if (target.isContentEditable) return true;
  return false;
}

export function useShortcut(key: string, callback: () => void, options: ShortcutOptions = {}) {
  const cbRef = useRef(callback);
  useEffect(() => {
    cbRef.current = callback;
  }, [callback]);

  const enabled = options.enabled ?? true;
  const needCmd = options.cmd ?? false;
  const needShift = options.shift ?? false;
  const needAlt = options.alt ?? false;

  useEffect(() => {
    if (!enabled) return;
    const handler = (e: KeyboardEvent) => {
      if (isEditableTarget(e.target)) return;
      if (e.key.toLowerCase() !== key.toLowerCase()) return;
      const cmdPressed = e.ctrlKey || e.metaKey;
      if (cmdPressed !== needCmd) return;
      if (e.shiftKey !== needShift) return;
      if (e.altKey !== needAlt) return;
      e.preventDefault();
      cbRef.current();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [key, enabled, needCmd, needShift, needAlt]);
}
