import { useCallback, useEffect, useState } from "react";

export function useEditorSelection() {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const select = useCallback((id: string) => setSelectedId(id), []);
  const clear = useCallback(() => setSelectedId(null), []);

  // Esc to clear
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      const target = e.target;
      if (target instanceof HTMLElement) {
        const tag = target.tagName.toLowerCase();
        if (tag === "input" || tag === "textarea" || target.isContentEditable) return;
      }
      setSelectedId(null);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  return { selectedId, select, clear };
}
