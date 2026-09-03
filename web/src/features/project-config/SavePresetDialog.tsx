import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { useCreateUserPreset } from "@/api/presets";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { DuplicateNameNotice } from "./DuplicateNameNotice";
import { type DraftChapter, toPresetChapters } from "./OutlineEditor";

/** 목차 편집기의 현재 구성을 내 프리셋으로 저장 - 같은 구성으로 다음 보고서를
 * 만들 때 "보고서 유형"에서 불러온다(2026-08-12 QA 2번의 확장). */
export function SavePresetDialog({
  chapters,
  onClose,
}: {
  chapters: DraftChapter[];
  onClose: () => void;
}) {
  const create = useCreateUserPreset();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  // 켜면 사내 전원의 프리셋 선택지에 뜬다(에이전트 공개와 같은 규약).
  const [isPublic, setIsPublic] = useState(false);
  // 같은 이름 충돌 - 이름을 바꾸거나, 한 번 더 눌러 기존 프리셋을 교체한다.
  // 덮어쓰기 길이 없으면 사용자가 손으로 "(2)(3)"을 붙여 복제가 쌓인다.
  const [duplicate, setDuplicate] = useState(false);
  const cleaned = toPresetChapters(chapters);
  const nSections = cleaned.reduce((n, ch) => n + ch.sections.length, 0);
  const valid = name.trim() !== "" && cleaned.length > 0;

  const save = async (overwrite: boolean) => {
    if (!valid || create.isPending) return;
    try {
      const saved = await create.mutateAsync({
        name: name.trim(),
        description: description.trim() || null,
        is_public: isPublic,
        chapters: cleaned,
        overwrite,
      });
      toast.success(overwrite ? `"${saved.name}" 프리셋 덮어씀` : `"${saved.name}" 프리셋 저장됨`, {
        description: "다음 프로젝트 생성 시 보고서 유형에서 선택할 수 있습니다.",
      });
      onClose();
    } catch (err) {
      if (err instanceof ApiError && err.code === "DUPLICATE_PRESET_NAME" && !overwrite) {
        setDuplicate(true);
        return;
      }
      toast.error("프리셋 저장 실패", {
        description: err instanceof ApiError ? err.message : "다시 시도해 주세요.",
      });
    }
  };

  return (
    <Dialog open onOpenChange={(o) => (!o ? onClose() : undefined)}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>내 프리셋으로 저장</DialogTitle>
          <DialogDescription>
            현재 목차({cleaned.length}장 {nSections}절)를 저장합니다. 제목 없는 절과 빈 장은
            빠집니다.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="preset-name">이름</Label>
            <Input
              id="preset-name"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setDuplicate(false);
              }}
              placeholder="예: 정책분석 표준 구성"
              disabled={create.isPending}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="preset-desc">한 줄 설명</Label>
            <Input
              id="preset-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="목록에서 이 구성을 알아볼 설명"
              disabled={create.isPending}
            />
          </div>
          <div className="flex items-start gap-3 rounded border border-border bg-bg-secondary p-3">
            <Switch
              id="preset-public"
              checked={isPublic}
              onCheckedChange={setIsPublic}
              disabled={create.isPending}
            />
            <div className="flex flex-col gap-0.5">
              <Label htmlFor="preset-public" className="cursor-pointer">
                공개
              </Label>
              <p className="text-xs text-fg-tertiary">
                {isPublic
                  ? "사내 모든 사람이 이 구성을 골라 새 보고서를 시작할 수 있습니다. 고치는 건 나만 할 수 있고, 남은 자기 것으로 가져가 고칩니다."
                  : "나만 씁니다. 켜면 사내 모든 사람의 보고서 유형 목록에 나타납니다."}
              </p>
            </div>
          </div>
          {cleaned.length === 0 ? (
            <p className="text-xs text-fg-danger">
              저장할 절이 없습니다. 제목 있는 절을 1개 이상 만들어 주세요.
            </p>
          ) : null}
          {duplicate ? <DuplicateNameNotice name={name.trim()} /> : null}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={create.isPending}>
            취소
          </Button>
          {duplicate ? (
            <Button
              variant="destructive"
              onClick={() => void save(true)}
              disabled={!valid || create.isPending}
            >
              {create.isPending ? "덮어쓰는 중…" : "덮어쓰기"}
            </Button>
          ) : (
            <Button onClick={() => void save(false)} disabled={!valid || create.isPending}>
              {create.isPending ? "저장 중…" : "저장"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
