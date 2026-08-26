import { useQueryClient } from "@tanstack/react-query";
import { FolderOpen, History, ListTree, PlayCircle } from "lucide-react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { progressKeys } from "@/api/progress";
import { useReopenProject } from "@/api/versions";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

// 재개 - 완료 보고서를 다시 열어 자료를 보강하고 빈 절·새 장을 이어 쓴다.
// 재개는 상태만 되돌린다(실행 안 함): 자료 올리고 목차 고칠 틈을 남기는 게 설계다.

export function ReopenDialog({
  projectId,
  open,
  onOpenChange,
}: {
  projectId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const reopen = useReopenProject();
  const qc = useQueryClient();

  const confirm = () => {
    reopen.mutate(projectId, {
      onSuccess: () => {
        void qc.invalidateQueries({ queryKey: progressKeys.snapshot(projectId) });
        onOpenChange(false);
        toast.success("보고서를 다시 열었습니다", {
          description:
            "현재 완성본은 버전으로 보관됐습니다. 자료·목차·옵션 어디든 손본 뒤 '이어서 진행'을 누르면 됩니다.",
        });
        // 자료 화면으로 **끌고 가지 않는다**. 다시 연 뒤에 무엇부터 할지는 사람이
        // 정한다 - 설계를 고칠 수도, 옵션을 바꿀 수도, 자료를 보탤 수도 있다
        // (2026-08-27 지적). 개요에 그대로 남으면 상태 패널의 여섯 줄이 그 갈림길을
        // 전부 보여준다.
      },
      onError: (err: unknown) => {
        const msg = err instanceof ApiError ? err.message : "다시 열지 못했습니다.";
        toast.error("재개 실패", { description: msg });
      },
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>보고서 다시 열기</DialogTitle>
          <DialogDescription>
            완료된 보고서에 자료를 보강하고 새 장·빈 절을 이어 쓰는 기능입니다. 이미 작성된 절은
            다시 쓰지 않습니다.
          </DialogDescription>
        </DialogHeader>
        <ul className="flex flex-col gap-2 text-sm text-fg-secondary">
          <li className="flex items-start gap-2">
            <History className="mt-0.5 h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
            지금 완성본이 버전으로 보관됩니다 - 나중에 무엇이 바뀌었는지 비교하고 그 시점 그대로
            내려받을 수 있습니다.
          </li>
          <li className="flex items-start gap-2">
            <FolderOpen className="mt-0.5 h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
            자료 화면에서 새 문서를 올리면 바로 색인됩니다.
          </li>
          <li className="flex items-start gap-2">
            <ListTree className="mt-0.5 h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
            설정에서 목차에 장·절을 더하거나 고칠 수 있습니다 - 기존 절의 정체성은 유지됩니다.
          </li>
          <li className="flex items-start gap-2">
            <PlayCircle className="mt-0.5 h-4 w-4 shrink-0 text-fg-tertiary" aria-hidden />
            준비가 되면 개요의 '이어서 진행'으로 작성을 시작합니다 - 색인과 검색 리허설을 거쳐 빈
            절만 씁니다.
          </li>
        </ul>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            취소
          </Button>
          <Button type="button" onClick={confirm} disabled={reopen.isPending}>
            {reopen.isPending ? "여는 중…" : "다시 열기"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
