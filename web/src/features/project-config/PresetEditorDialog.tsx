import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import {
  type UserPreset,
  useCreateUserPreset,
  usePresetDetail,
  useUpdateUserPreset,
} from "@/api/presets";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
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
import {
  type DraftChapter,
  emptyChapter,
  fromPreset,
  OutlineEditor,
  toPresetChapters,
} from "./OutlineEditor";

/** 내 목차 프리셋 만들기/편집 - 생성 폼과 완전히 같은 편집기(OutlineEditor)를 쓴다.
 * 미리 구성을 준비해 두고, 프로젝트 생성 화면의 보고서 유형에서 불러온다. */
export function PresetEditorDialog({
  existing,
  onClose,
}: {
  existing?: UserPreset;
  onClose: () => void;
}) {
  const create = useCreateUserPreset();
  const update = useUpdateUserPreset();
  // 편집이면 서버에서 골격을 받아 채운다 - 목록 응답에는 chapters가 없다.
  const detail = usePresetDetail(existing?.key ?? null);
  const [name, setName] = useState(existing?.name ?? "");
  const [description, setDescription] = useState("");
  const [chapters, setChapters] = useState<DraftChapter[] | null>(
    existing ? null : [emptyChapter()],
  );
  useEffect(() => {
    if (existing && detail.data && chapters === null) {
      setChapters(fromPreset(detail.data));
      // 설명은 상세 응답이 진실 - 목록의 desc는 빈 설명일 때 폴백 문구가 실린다.
      setDescription(detail.data.desc);
    }
  }, [existing, detail.data, chapters]);

  const pending = create.isPending || update.isPending;
  const cleaned = chapters ? toPresetChapters(chapters) : [];
  const valid = name.trim() !== "" && cleaned.length > 0;

  const save = async () => {
    if (!valid || pending) return;
    const body = {
      name: name.trim(),
      description: description.trim() || null,
      chapters: cleaned,
    };
    try {
      if (existing) {
        await update.mutateAsync({ id: existing.id, ...body });
        toast.success(`"${body.name}" 저장됨`);
      } else {
        await create.mutateAsync(body);
        toast.success(`"${body.name}" 만들어짐`, {
          description: "프로젝트 생성 시 보고서 유형에서 선택할 수 있습니다.",
        });
      }
      onClose();
    } catch (err) {
      toast.error("저장 실패", {
        description: err instanceof ApiError ? err.message : "다시 시도해 주세요.",
      });
    }
  };

  return (
    <Dialog open onOpenChange={(o) => (!o ? onClose() : undefined)}>
      <DialogContent className="max-h-[90vh] max-w-4xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{existing ? "편집" : "새로 만들기"} - 내 목차 프리셋</DialogTitle>
          <DialogDescription>
            장·절 구성과 담당 에이전트 배정을 저장해 두고, 프로젝트 생성 화면의 보고서 유형에서
            불러옵니다.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="upreset-name">이름</Label>
              <Input
                id="upreset-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="예: 정책분석 표준 구성"
                disabled={pending}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="upreset-desc">한 줄 설명</Label>
              <Input
                id="upreset-desc"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="목록에서 이 구성을 알아볼 설명"
                disabled={pending}
              />
            </div>
          </div>
          {chapters === null ? (
            <LoadingSkeleton variant="card" count={2} />
          ) : (
            <OutlineEditor
              chapters={chapters}
              onChange={setChapters}
              defaultExpanded={existing ? "none" : "all"}
              headerInfo={
                <>
                  저장되는 것은 <span className="font-medium text-fg">구성</span>입니다 - 장·절
                  제목, 작성 방향, 핵심 포인트, 담당 에이전트 배정 · 유효한 절{" "}
                  {cleaned.reduce((n, ch) => n + ch.sections.length, 0)}개
                </>
              }
            />
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={pending}>
            취소
          </Button>
          <Button onClick={() => void save()} disabled={!valid || pending}>
            {pending ? "저장 중…" : "저장"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
