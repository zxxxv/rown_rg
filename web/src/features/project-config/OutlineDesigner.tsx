import { BookmarkPlus, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useFormContext, useWatch } from "react-hook-form";
import { usePresetDetail } from "@/api/presets";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { Button } from "@/components/ui/button";
import {
  type DraftChapter,
  emptyChapter,
  fromOutline,
  fromPreset,
  OutlineEditor,
  toOutline,
} from "./OutlineEditor";
import { SavePresetDialog } from "./SavePresetDialog";
import type { ProjectFormValues } from "./schema";

// ─── 목차 설계 - 프리셋 골격을 펼쳐 장·절·에이전트를 직접 확정하는 폼 래퍼 ───
// 목차는 사람이 무조건 만든다(2026-08-03 확정): AI 목차 설계 없음. 확정된
// config.outline이 그대로 실행되고, 그 목차 순서로 자료 조사가 진행된다.
// 편집기 본체는 OutlineEditor(순수) - 내 프리셋 관리 화면과 공유한다.

export function OutlineDesigner() {
  const { setValue, getValues } = useFormContext<ProjectFormValues>();
  const preset = useWatch<ProjectFormValues, "config.preset">({ name: "config.preset" });
  const rules = useWatch<ProjectFormValues, "config.rules">({ name: "config.rules" }) ?? [];
  const detailQuery = usePresetDetail(preset ?? null);

  const [chapters, setChapters] = useState<DraftChapter[]>([]);
  // 골격을 통째로 갈아끼울 때 편집기를 리마운트해 펼침 상태를 리셋한다
  // (빈 시작=펼침, 골격 로드=전부 접힘 - 35섹션 프리셋 스크롤 방지).
  const [editorEpoch, setEditorEpoch] = useState(0);
  const [initialExpand, setInitialExpand] = useState<"all" | "none">("none");
  const remount = useCallback((mode: "all" | "none") => {
    setInitialExpand(mode);
    setEditorEpoch((v) => v + 1);
  }, []);

  // 폼과 동기화. 목차는 필수(AI 설계 없음) - 유효한 절이 없으면 undefined가
  // 저장되고 폼 제출 단계에서 차단된다.
  const sync = useCallback(
    (next: DraftChapter[]) => {
      setChapters(next);
      setValue("config.outline", toOutline(next), { shouldDirty: true });
    },
    [setValue],
  );

  // 마운트: 기존 config.outline(수정 모드·재방문) 복원 → 없으면 프리셋 골격
  // (상세 도착 시) 또는 자유 주제 빈 장 1개로 시작. 목차 편집기는 항상 펼쳐진다.
  const mountedRef = useRef(false);
  const prevPresetRef = useRef<string | null | undefined>(undefined);
  const pendingSkeletonRef = useRef<string | null>(null);
  const startBlank = useCallback(() => {
    const blank = emptyChapter();
    sync([blank]);
    remount("all");
  }, [sync, remount]);
  useEffect(() => {
    if (!mountedRef.current) {
      mountedRef.current = true;
      prevPresetRef.current = preset ?? null;
      const existing = getValues("config.outline");
      if (existing && existing.chapters.length > 0) {
        // fromOutline: 서버 발급 id를 _id로 잇는다 - 여기서 id를 새로 찍으면
        // 저장 시 전 절의 정체성(계획·리허설·본문 행)이 리셋된다.
        setChapters(fromOutline(existing));
        return;
      }
      if (preset) pendingSkeletonRef.current = preset;
      else startBlank();
      return;
    }
    if (prevPresetRef.current !== (preset ?? null)) {
      prevPresetRef.current = preset ?? null;
      if (preset) pendingSkeletonRef.current = preset;
      else startBlank();
    }
  }, [preset, getValues, startBlank]);

  // 예약된 골격 로드 - 프리셋 상세가 도착하는 시점에 편집기를 채운다(전부 접힘).
  useEffect(() => {
    if (!preset || !detailQuery.data) return;
    if (pendingSkeletonRef.current !== preset) return;
    pendingSkeletonRef.current = null;
    sync(fromPreset(detailQuery.data));
    remount("none");
  }, [preset, detailQuery.data, sync, remount]);

  const totalSections = chapters.reduce(
    (n, ch) => n + ch.sections.filter((s) => s.title.trim()).length,
    0,
  );
  const [savingPreset, setSavingPreset] = useState(false);

  if (preset && detailQuery.isLoading && chapters.length === 0) {
    return <LoadingSkeleton variant="card" count={2} />;
  }

  return (
    <>
      <OutlineEditor
        key={editorEpoch}
        chapters={chapters}
        onChange={sync}
        previewRules={rules}
        defaultExpanded={initialExpand}
        headerInfo={
          <>
            목차는 <span className="font-medium text-fg">직접 확정</span>합니다 (AI가 목차를 만들지
            않음) · 확정된 목차 순서로 자료를 조사합니다 · 유효한 절 {totalSections}개
          </>
        }
        headerActions={
          <>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={totalSections === 0}
              onClick={() => setSavingPreset(true)}
              title="현재 목차 구성을 저장해 다음 프로젝트에서 보고서 유형으로 불러옵니다"
            >
              <BookmarkPlus className="mr-1 h-3.5 w-3.5" aria-hidden />내 프리셋으로 저장
            </Button>
            {preset ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={!detailQuery.data}
                onClick={() => {
                  if (!detailQuery.data) return;
                  sync(fromPreset(detailQuery.data));
                  remount("none");
                }}
              >
                <RotateCcw className="mr-1 h-3.5 w-3.5" aria-hidden />
                프리셋 골격으로 리셋
              </Button>
            ) : null}
          </>
        }
      />
      {savingPreset ? (
        <SavePresetDialog chapters={chapters} onClose={() => setSavingPreset(false)} />
      ) : null}
    </>
  );
}
