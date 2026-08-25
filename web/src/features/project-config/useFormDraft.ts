import { useEffect, useState } from "react";
import type { UseFormReturn } from "react-hook-form";
import { ProjectFormSchema, type ProjectFormValues } from "./schema";

// ─── 생성 폼 초안 보존 ───
// 폼은 순수 메모리 상태라 프롬프트 화면으로 잠깐 나가거나 새로고침만 해도
// 주제·제목·목차 편집이 통째로 날아갔다(35절 프리셋을 손봤다면 그게 전부).
// 브라우저에만 저장하고, 저장이 끝나면 지운다.
//
// **복원은 effect가 아니라 폼의 초기값으로 한다(2026-08-25 수정).** effect로
// form.reset(draft)을 하면 늦는다 - React는 자식 effect를 부모보다 먼저 돌리므로
// OutlineDesigner의 마운트 effect가 "outline이 없다"고 판단해 프리셋 골격 로드를
// 예약하고, 상세가 도착하는 순간 **복원된 목차를 골격으로 덮어썼다**. 실측(나갔다
// 오기 관통): 제목·주제는 살아남고 목차만 프리셋 원본으로 되돌아갔다.

const DRAFT_KEY = "rown:project-draft:v1";
const DRAFT_AT_KEY = "rown:project-draft:v1:at";
// 저장 폭주 방지 - 타이핑 중 매 글자 저장할 이유가 없다.
const SAVE_DEBOUNCE_MS = 800;

export interface ProjectDraft {
  values: ProjectFormValues;
  /** 마지막 저장 시각(ISO/UTC) - 표시할 때만 지역 시간으로 바꾼다 */
  savedAt: string | null;
}

/** 저장된 초안. 폼의 defaultValues로 바로 쓴다 - 첫 렌더부터 값이 들어 있어야
 * 목차 편집기가 골격을 덮어쓰지 않는다. */
export function readProjectDraft(): ProjectDraft | null {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    if (!raw) return null;
    // 스키마가 바뀐 옛 초안은 버린다 - 억지로 복원하면 폼이 깨진 채 뜬다.
    const parsed = ProjectFormSchema.safeParse(JSON.parse(raw));
    if (!parsed.success) return null;
    return { values: parsed.data, savedAt: localStorage.getItem(DRAFT_AT_KEY) };
  } catch {
    return null;
  }
}

export function clearProjectDraft(): void {
  try {
    localStorage.removeItem(DRAFT_KEY);
    localStorage.removeItem(DRAFT_AT_KEY);
  } catch {
    // 저장소를 못 쓰는 환경(프라이빗 모드 등)에서도 폼은 그대로 동작해야 한다.
  }
}

/** 사람이 알아볼 만한 내용이 있는 초안인가 - 배너를 띄울지의 기준.
 *
 * 초안은 프리셋 골격이 로드되기만 해도(setValue) 저장된다. 그래서 "초안 비우기"로
 * 비운 직후에도 빈 폼의 초안이 곧바로 다시 쌓이는데, 그걸 두고 다음 방문에
 * "복원했습니다"라고 하면 복원할 것도 없이 배너만 뜬다. 값은 그대로 복원하되(빈
 * 초안이면 기본값과 같아 무해하다) 알림은 제목·주제가 있을 때만 한다. */
function looksWritten(values: ProjectFormValues): boolean {
  return values.title.trim().length > 0 || values.topic.trim().length > 0;
}

export interface ProjectDraftState {
  /** 복원된 초안이 있으면 true - 화면이 "복원했습니다 / 새로 시작"을 알린다 */
  restored: boolean;
  /** 복원한 초안이 마지막으로 저장된 시각(ISO) */
  savedAt: string | null;
  discard: () => void;
}

/**
 * 생성 모드에서만 초안을 저장한다(수정 모드는 서버 값이 진실).
 * 복원 자체는 호출부가 `readProjectDraft()`로 초기값을 만들어 넘긴 뒤라, 여기서는
 * 저장과 배너 상태만 맡는다.
 *
 * 복원을 조용히 하지는 않는다 - 새 프로젝트를 만들려는데 옛 내용이 채워져 있으면
 * 그게 더 혼란스럽다. 배너로 알리고 한 번에 비울 수 있게 한다.
 */
export function useProjectDraft(
  form: UseFormReturn<ProjectFormValues>,
  enabled: boolean,
  initialDraft: ProjectDraft | null,
): ProjectDraftState {
  const [restored, setRestored] = useState(
    initialDraft !== null && looksWritten(initialDraft.values),
  );

  useEffect(() => {
    if (!enabled) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const sub = form.watch((values) => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        try {
          localStorage.setItem(DRAFT_KEY, JSON.stringify(values));
          localStorage.setItem(DRAFT_AT_KEY, new Date().toISOString());
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
    savedAt: initialDraft?.savedAt ?? null,
    discard: () => {
      clearProjectDraft();
      setRestored(false);
      // 초안이 폼의 초기값이라 되돌리려면 폼을 새로 세워야 한다(리로드가 가장 정직).
      window.location.reload();
    },
  };
}
