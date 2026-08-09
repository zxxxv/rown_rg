import { createContext, useCallback, useContext, useState } from "react";

export interface RewriteDialogValue {
  open: boolean;
  componentId: string | null;
  openRewrite: (componentId: string) => void;
  closeRewrite: () => void;
}

const RewriteDialogContext = createContext<RewriteDialogValue | null>(null);

export function useRewriteDialogState(): RewriteDialogValue {
  const [open, setOpen] = useState(false);
  const [componentId, setComponentId] = useState<string | null>(null);

  const openRewrite = useCallback((id: string) => {
    setComponentId(id);
    setOpen(true);
  }, []);
  const closeRewrite = useCallback(() => setOpen(false), []);

  return { open, componentId, openRewrite, closeRewrite };
}

export { RewriteDialogContext };

export function useRewriteDialog(): RewriteDialogValue {
  const ctx = useContext(RewriteDialogContext);
  if (!ctx) {
    throw new Error("useRewriteDialog must be used inside <RewriteDialogProvider>");
  }
  return ctx;
}
