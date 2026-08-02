import { BookOpen, Plus, Save, Trash2, Wand2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import {
  type PromptKind,
  useCreatePersonalPrompt,
  useDeletePersonalPrompt,
  usePersonalPrompt,
  useSystemPrompt,
  useUpdatePersonalPrompt,
} from "@/api/prompts";
import type { PromptRef } from "@/api/types";
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
import { Textarea } from "@/components/ui/textarea";

const KIND_LABEL: Record<PromptKind, string> = { agent: "에이전트", rule: "작성 규칙" };

/** 프롬프트 파일 노드 상세 — 개인은 편집 가능, 시스템은 읽기전용. */
export function PromptBody({ prompt }: { prompt: PromptRef }) {
  if (prompt.scope === "system") return <SystemPromptView prompt={prompt} />;
  return <PersonalPromptEditor prompt={prompt} />;
}

function SystemPromptView({ prompt }: { prompt: PromptRef }) {
  const query = useSystemPrompt(prompt.kind, prompt.ref);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 rounded border border-border bg-bg-secondary px-3 py-2 text-xs text-fg-tertiary">
        <BookOpen className="h-3.5 w-3.5" aria-hidden />
        시스템 {KIND_LABEL[prompt.kind]} — 읽기 전용입니다. 내 것으로 만들려면 개인 프롬프트로
        복제하세요.
      </div>
      {query.isLoading ? (
        <p className="text-sm text-fg-tertiary">불러오는 중…</p>
      ) : query.isError ? (
        <p className="text-sm text-fg-danger">불러오지 못했습니다.</p>
      ) : (
        <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded border border-border bg-bg p-3 font-mono text-xs leading-relaxed text-fg-secondary">
          {query.data?.content}
        </pre>
      )}
    </div>
  );
}

function PersonalPromptEditor({ prompt }: { prompt: PromptRef }) {
  const query = usePersonalPrompt(prompt.ref);
  const update = useUpdatePersonalPrompt(prompt.ref);
  const del = useDeletePersonalPrompt();
  const [content, setContent] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);

  const loaded = query.data;
  useEffect(() => {
    if (loaded) setContent(loaded.content);
  }, [loaded]);

  const dirty = loaded !== undefined && content !== loaded.content;

  const onSave = async () => {
    try {
      await update.mutateAsync({ content });
      toast.success("저장했습니다.");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "저장에 실패했습니다.";
      toast.error("저장 실패", { description: msg });
    }
  };

  const onDelete = async () => {
    try {
      await del.mutateAsync(prompt.ref);
      toast.success("삭제했습니다.");
      setConfirmOpen(false);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "삭제에 실패했습니다.";
      toast.error("삭제 실패", { description: msg });
    }
  };

  if (query.isLoading) return <p className="text-sm text-fg-tertiary">불러오는 중…</p>;
  if (query.isError || !loaded)
    return <p className="text-sm text-fg-danger">프롬프트를 불러오지 못했습니다.</p>;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 rounded border border-dashed border-border bg-bg-secondary px-3 py-2 text-xs text-fg-tertiary">
        <Wand2 className="h-3.5 w-3.5" aria-hidden />내 {KIND_LABEL[loaded.kind]}
        {loaded.base_ref ? ` · 시스템 "${loaded.base_ref}" 덮어쓰기` : " · 신규"} — 보고서 생성 시
        이 내용이 시스템 기본값보다 우선 적용됩니다.
      </div>
      <Textarea
        value={content}
        onChange={(e) => setContent(e.target.value)}
        rows={16}
        className="font-mono text-xs leading-relaxed"
        aria-label="프롬프트 본문"
      />
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => void onSave()} disabled={!dirty || update.isPending}>
          <Save className="mr-1 h-4 w-4" />
          {update.isPending ? "저장 중…" : "저장"}
        </Button>
        <Button
          variant="outline"
          className="text-fg-danger"
          onClick={() => setConfirmOpen(true)}
          disabled={del.isPending}
        >
          <Trash2 className="mr-1 h-4 w-4" />
          삭제
        </Button>
      </div>

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>프롬프트를 삭제할까요?</DialogTitle>
            <DialogDescription>
              "{loaded.name}"이(가) 영구 삭제됩니다. 삭제 후에는 시스템 기본값이 다시 적용됩니다.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              취소
            </Button>
            <Button variant="destructive" onClick={() => void onDelete()} disabled={del.isPending}>
              {del.isPending ? "삭제 중…" : "삭제"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** '내 에이전트'/'내 작성 규칙' 폴더에서만 노출하는 새 프롬프트 생성 버튼. */
export function PromptCreateButton({ folderId }: { folderId: string }) {
  const kind: PromptKind | null =
    folderId === "me-agents" ? "agent" : folderId === "me-rules" ? "rule" : null;
  const create = useCreatePersonalPrompt();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [content, setContent] = useState("");

  if (kind === null) return null;

  const reset = () => {
    setName("");
    setContent("");
  };

  const onCreate = async () => {
    if (!name.trim() || !content.trim()) return;
    try {
      await create.mutateAsync({ kind, name: name.trim(), content: content.trim() });
      toast.success(`${KIND_LABEL[kind]} "${name.trim()}"을(를) 만들었습니다.`);
      setOpen(false);
      reset();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "생성에 실패했습니다.";
      toast.error("생성 실패", { description: msg });
    }
  };

  return (
    <>
      <Button onClick={() => setOpen(true)}>
        <Plus className="mr-1 h-4 w-4" />새 {KIND_LABEL[kind]}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>새 {KIND_LABEL[kind]} 만들기</DialogTitle>
            <DialogDescription>
              나만의 {KIND_LABEL[kind]}입니다. 보고서 생성 시 시스템 기본값보다 우선 적용됩니다.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="new-prompt-name">이름</Label>
              <Input
                id="new-prompt-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={kind === "agent" ? "예: 우리회사 시장분석가" : "예: 사내 문체 규칙"}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="new-prompt-content">내용</Label>
              <Textarea
                id="new-prompt-content"
                value={content}
                onChange={(e) => setContent(e.target.value)}
                rows={10}
                className="font-mono text-xs"
                placeholder="프롬프트/규칙 본문을 입력하세요."
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              취소
            </Button>
            <Button
              onClick={() => void onCreate()}
              disabled={create.isPending || !name.trim() || !content.trim()}
            >
              {create.isPending ? "만드는 중…" : "만들기"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
