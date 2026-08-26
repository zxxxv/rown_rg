import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { useSaveManualVersion } from "@/api/versions";
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

// 수동 버전 저장 - 자동 스냅샷 사이의 편집 구간을 사람이 직접 찍는 체크포인트.
// 이름을 받는 이유: 이정표에 이름이 없으면 v번호일 뿐이라 나중에 못 찾는다.
// 서버는 reason에 "manual:<꼬리표>"로 싣고 꼬리표 상한이 20자다(ManualVersionRequest).
export const VERSION_NOTE_MAX = 20;

export function SaveVersionDialog({
  projectId,
  onClose,
}: {
  projectId: string;
  onClose: () => void;
}) {
  const save = useSaveManualVersion(projectId);
  const [note, setNote] = useState("");
  const trimmed = note.trim();

  const submit = async () => {
    if (save.isPending) return;
    try {
      // 이름은 선택이다 - 빈 칸이면 꼬리표 없는 "직접 저장"으로 남는다.
      const res = await save.mutateAsync(trimmed || undefined);
      if (res.created) {
        toast.success(`v${res.version_no} 저장됨`, {
          // 이름을 문장에 끼우면 조사가 이름 끝 글자에 따라 달라진다(전/표) - 이름은
          // 따로 떼어 붙인다.
          description: trimmed ? `이름: ${trimmed}` : "현재 본문을 버전으로 남겼습니다.",
        });
      } else {
        // 내용 지문이 같으면 서버가 새 버전을 만들지 않는다 - 실패가 아니라 무해한 중복.
        toast(`변경 없음 - 최신 버전(v${res.version_no})과 같은 내용입니다.`);
      }
      onClose();
    } catch (err) {
      toast.error("버전 저장에 실패했습니다.", {
        description: err instanceof ApiError ? err.message : "다시 시도해 주세요.",
      });
    }
  };

  return (
    <Dialog open onOpenChange={(o) => (!o ? onClose() : undefined)}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>이 상태를 버전으로 저장</DialogTitle>
          <DialogDescription>
            지금 본문을 그대로 얼려 둡니다. 이후 어떻게 고쳐도 이 지점으로 절마다 되돌릴 수
            있습니다.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between">
            <Label htmlFor="version-note">이름 (선택)</Label>
            <span className="text-xs tabular-nums text-fg-tertiary">
              {trimmed.length}/{VERSION_NOTE_MAX}
            </span>
          </div>
          <Input
            id="version-note"
            value={note}
            maxLength={VERSION_NOTE_MAX}
            autoFocus
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void submit();
              }
            }}
            placeholder="예: 부장님 검토 전"
            disabled={save.isPending}
          />
          <p className="text-xs text-fg-tertiary">
            목록에서 이 지점을 알아볼 짧은 이름입니다. 비워 두면 날짜로만 남습니다.
          </p>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={onClose} disabled={save.isPending}>
            취소
          </Button>
          <Button onClick={() => void submit()} disabled={save.isPending}>
            {save.isPending ? "저장 중…" : "저장"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
