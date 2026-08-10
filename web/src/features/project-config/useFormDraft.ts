import { useEffect, useRef, useState } from "react";
import type { UseFormReturn } from "react-hook-form";
import { ProjectFormSchema, type ProjectFormValues } from "./schema";

// ─── 생성 폼 초안 보존 ───
// 폼은 순수 메모리 상태라 프롬프트 화면으로 잠깐 나가거나 새로고침만 해도
// 주제·제목·목차 편집이 통째로 날아갔다(35절 프리셋을 손봤다면 그게 전부).
// 브라우저에만 저장하고, 저장이 끝나면 지운다.

const DRAFT_KEY = "rown:project-draft:v1";
// 저장 폭주 방지 - 타이핑 중 매 글자 저장할 이유가 없다.
const SAVE_DEBOUNCE_MS = 800;

function readDraft(): ProjectFormValues | null {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    // 스키마가 바뀐 옛 초안은 버린다 - 억지로 복원하면 폼이 깨진 채 뜬다.
    const parsed = ProjectFormSchema.safeParse(JSON.parse(raw));
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}

export function clearProjectDraft(): void {
  try {
    localStorage.removeItem(DRAFT_KEY);
  } catch {
    // 저장소를 못 쓰는 환경(프라이빗 모드 등)에서도 폼은 그대로 동작해야 한다.
  }
}

export interface ProjectDraftState {
  /** 복원된 초안이 있으면 true - 화면이 "복원했습니다 / 새로 시작"을 알린다 */
  restored: boolean;
  discard: () => void;
}

/**
 * 생성 모드에서만 초안을 저장·복원한다(수정 모드는 서버 값이 진실).
 *
 * 복원은 조용히 하지 않는다 - 사용자가 새 프로젝트를 만들려는데 옛 내용이 채워져
 * 있으면 그게 더 혼란스럽다. 배너로 알리고 한 번에 비울 수 있게 한다.
 */
export function useProjectDraft(
  form: UseFormReturn<ProjectFormValues>,
  enabled: boolean,
): ProjectDraftState {
  const [restored, setRestored] = useState(false);
  const mounted = useRef(false);

  useEffect(() => {
    if (!enabled || mounted.current) return;
    mounted.current = true;
    const draft = readDraft();
    if (draft) {
      form.reset(draft);
      setRestored(true);
    }
  }, [enabled, form]);

  useEffect(() => {
    if (!enabled) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const sub = form.watch((values) => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        try {
          localStorage.setItem(DRAFT_KEY, JSON.stringify(values));
        } catch {
          // 용량 초과·프라이빗 모드 - 초안은 편의 기능이라 조용히 포기한다.
        }
      }, SAVE_DEBOUNCE_MS);
    });
    return () => {
      if (timer) clearTimeout(timer);
      sub.unsubscribe();
    };
  }, [enabled, form]);

  return {
    restored,
    discard: () => {
      clearProjectDraft();
      setRestored(false);
      window.location.reload();
    },
  };
}
