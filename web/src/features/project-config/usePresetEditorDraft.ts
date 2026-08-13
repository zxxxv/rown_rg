import { useEffect, useRef } from "react";
import { type DraftChapter, type DraftSection, draftId } from "./OutlineEditor";

// ─── 프리셋 편집 초안 보존 ───
// 프리셋 편집 다이얼로그는 메모리 상태라 다른 페이지에 다녀오면 쓰던 목차가
// 통째로 날아갔다(2026-08-13 사용자 확인). 프롬프트 초안(usePromptDraft)과
// 같은 원칙 - 브라우저에만 저장, 저장 성공 시 삭제, 신규 작성만(편집은 서버가 진실).

const KEY = "rown:preset-editor-draft:v1";
const SAVE_DEBOUNCE_MS = 800;

export interface PresetEditorDraft {
  name: string;
  description: string;
  chapters: DraftChapter[];
}

/** 쓸 만한 내용이 하나라도 있는가 - 빈 초안은 저장·복원할 이유가 없다. */
export function presetDraftHasContent(d: PresetEditorDraft): boolean {
  return (
    d.name.trim() !== "" ||
    d.description.trim() !== "" ||
    d.chapters.some(
      (ch) =>
        ch.title.trim() !== "" ||
        ch.sections.some(
          (s) =>
            s.title.trim() !== "" ||
            s.direction.trim() !== "" ||
            s.key_points.some((k) => k.trim() !== "") ||
            s.analysts.length > 0,
        ),
    )
  );
}

function strArray(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

function toSection(v: unknown): DraftSection {
  const s = (typeof v === "object" && v !== null ? v : {}) as Partial<DraftSection>;
  return {
    _id: typeof s._id === "string" ? s._id : draftId(),
    title: typeof s.title === "string" ? s.title : "",
    direction: typeof s.direction === "string" ? s.direction : "",
    key_points: strArray(s.key_points),
    analysts: strArray(s.analysts),
  };
}

function toChapter(v: unknown): DraftChapter {
  const ch = (typeof v === "object" && v !== null ? v : {}) as Partial<DraftChapter>;
  return {
    _id: typeof ch._id === "string" ? ch._id : draftId(),
    title: typeof ch.title === "string" ? ch.title : "",
    sections: Array.isArray(ch.sections) ? ch.sections.map(toSection) : [],
  };
}

export function readPresetEditorDraft(): PresetEditorDraft | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PresetEditorDraft>;
    // 모양이 깨진 옛 초안은 버린다 - 억지로 복원하면 편집기가 깨진 채 뜬다.
    if (typeof parsed !== "object" || parsed === null || !Array.isArray(parsed.chapters)) {
      return null;
    }
    const draft: PresetEditorDraft = {
      name: typeof parsed.name === "string" ? parsed.name : "",
      description: typeof parsed.description === "string" ? parsed.description : "",
      chapters: parsed.chapters.map(toChapter),
    };
    return presetDraftHasContent(draft) ? draft : null;
  } catch {
    return null;
  }
}

export function clearPresetEditorDraft(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // 저장소를 못 쓰는 환경(프라이빗 모드 등)에서도 편집기는 그대로 동작해야 한다.
  }
}

/** 초안 자동 저장 - enabled(신규 작성)일 때만, 타이핑 멎은 뒤 한 번. */
export function usePresetEditorDraftSave(enabled: boolean, draft: PresetEditorDraft): void {
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => {
    if (!enabled) return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      try {
        if (presetDraftHasContent(draft)) localStorage.setItem(KEY, JSON.stringify(draft));
        // 도로 비웠으면 초안도 지운다 - 빈 초안이 다음 열기에서 되살아나면 혼란이다.
        else localStorage.removeItem(KEY);
      } catch {
        // 용량 초과·프라이빗 모드 - 초안은 편의 기능이라 조용히 포기한다.
      }
    }, SAVE_DEBOUNCE_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [enabled, draft]);
}
