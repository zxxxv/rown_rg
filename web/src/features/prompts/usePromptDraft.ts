import { useEffect, useRef } from "react";
import type { PromptKind } from "@/api/prompts";

// ─── 프롬프트 작성 초안 보존 ───
// 다이얼로그는 순수 메모리 상태라 실수로 닫거나 새로고침하면 쓰던 페르소나가
// 통째로 날아갔다(2026-08-12 QA: 중간저장 요청). 프로젝트 폼 초안(useFormDraft)과
// 같은 원칙 - 브라우저에만 저장, 저장 성공 시 삭제, 신규 작성만(편집은 서버가 진실).

const SAVE_DEBOUNCE_MS = 800;

const keyOf = (kind: PromptKind) => `rown:prompt-draft:v1:${kind}`;

export interface PromptDraft {
  name: string;
  content: string;
  sections: Record<string, string>;
  freeform: boolean;
  cat: string;
  description: string;
  minChars: string;
  maxChars: string;
  baseRef: string;
}

/** 쓸 만한 내용이 하나라도 있는가 - 빈 초안은 저장·복원할 이유가 없다. */
export function draftHasContent(d: PromptDraft): boolean {
  return (
    d.name.trim() !== "" ||
    d.content.trim() !== "" ||
    Object.values(d.sections).some((v) => v.trim() !== "") ||
    d.description.trim() !== ""
  );
}

export function readPromptDraft(kind: PromptKind): PromptDraft | null {
  try {
    const raw = localStorage.getItem(keyOf(kind));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PromptDraft>;
    if (typeof parsed !== "object" || parsed === null) return null;
    // 모양이 깨진 옛 초안은 버린다 - 억지로 복원하면 폼이 깨진 채 뜬다.
    const draft: PromptDraft = {
      name: typeof parsed.name === "string" ? parsed.name : "",
      content: typeof parsed.content === "string" ? parsed.content : "",
      sections:
        typeof parsed.sections === "object" && parsed.sections !== null
          ? Object.fromEntries(
              Object.entries(parsed.sections).filter(([, v]) => typeof v === "string"),
            )
          : {},
      freeform: typeof parsed.freeform === "boolean" ? parsed.freeform : true,
      cat: typeof parsed.cat === "string" ? parsed.cat : "",
      description: typeof parsed.description === "string" ? parsed.description : "",
      minChars: typeof parsed.minChars === "string" ? parsed.minChars : "",
      maxChars: typeof parsed.maxChars === "string" ? parsed.maxChars : "",
      baseRef: typeof parsed.baseRef === "string" ? parsed.baseRef : "",
    };
    return draftHasContent(draft) ? draft : null;
  } catch {
    return null;
  }
}

export function clearPromptDraft(kind: PromptKind): void {
  try {
    localStorage.removeItem(keyOf(kind));
  } catch {
    // 저장소를 못 쓰는 환경(프라이빗 모드 등)에서도 폼은 그대로 동작해야 한다.
  }
}

/** 초안 자동 저장 - enabled(신규 작성)일 때만, 타이핑 멎은 뒤 한 번. */
export function usePromptDraftSave(kind: PromptKind, enabled: boolean, draft: PromptDraft): void {
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => {
    if (!enabled) return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      try {
        if (draftHasContent(draft)) localStorage.setItem(keyOf(kind), JSON.stringify(draft));
        // 도로 비웠으면 초안도 지운다 - 빈 초안이 다음 열기에서 되살아나면 혼란이다.
        else localStorage.removeItem(keyOf(kind));
      } catch {
        // 용량 초과·프라이빗 모드 - 초안은 편의 기능이라 조용히 포기한다.
      }
    }, SAVE_DEBOUNCE_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [kind, enabled, draft]);
}
