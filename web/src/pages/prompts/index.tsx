import { ChevronDown, ChevronRight, FilePlus2, Pencil, ScrollText, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import {
  type PersonalPrompt,
  type PromptKind,
  useCreatePersonalPrompt,
  useDeletePersonalPrompt,
  useListPersonalPrompts,
  useListSystemPrompts,
  useUpdatePersonalPrompt,
} from "@/api/prompts";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
import { useAuth } from "@/hooks/useAuth";

const KIND_META: Record<PromptKind, { title: string; desc: string; placeholder: string }> = {
  agent: {
    title: "내 분석 에이전트",
    desc: "섹션에 배정하는 분석가 페르소나. 내 것을 만들면 목차 설계에서 선택할 수 있습니다.",
    placeholder: "예: 우리회사 시장분석가",
  },
  rule: {
    title: "내 작성 규칙",
    desc: "보고서 문체·서식 규칙. 내 것을 만들면 시스템 기본 규칙보다 우선 적용됩니다.",
    placeholder: "예: 사내 문체 규칙",
  },
};

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

export default function PromptsPage() {
  const { user, logout } = useAuth();
  return (
    <AppShell
      user={user ? { name: user.name, role: user.role } : null}
      onLogout={() => void logout()}
    >
      <div className="flex flex-col gap-6">
        <header className="flex flex-col gap-1">
          <h1 className="flex items-center gap-2 text-3xl font-semibold text-fg">
            <ScrollText className="h-7 w-7 text-fg-secondary" aria-hidden />
            프롬프트 관리
          </h1>
          <p className="text-sm text-fg-secondary">
            보고서 생성에 쓰이는 내 분석 에이전트·작성 규칙을 직접 만들고 관리합니다. 내 것을 만들면
            같은 이름의 시스템 기본값을 덮어쓰거나 새로 추가됩니다.
          </p>
        </header>

        <KindSection kind="agent" />
        <KindSection kind="rule" />
      </div>
    </AppShell>
  );
}

function KindSection({ kind }: { kind: PromptKind }) {
  const meta = KIND_META[kind];
  const personal = useListPersonalPrompts(kind);
  const del = useDeletePersonalPrompt();
  const [editing, setEditing] = useState<PersonalPrompt | null>(null);
  const [creating, setCreating] = useState(false);

  const onDelete = (p: PersonalPrompt) => {
    del.mutate(p.id, {
      onSuccess: () => toast.success(`"${p.name}" 삭제됨`),
      onError: (err) =>
        toast.error("삭제 실패", { description: errMsg(err, "다시 시도해 주세요.") }),
    });
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
        <div>
          <CardTitle className="text-base">{meta.title}</CardTitle>
          <CardDescription>{meta.desc}</CardDescription>
        </div>
        <Button size="sm" onClick={() => setCreating(true)}>
          <FilePlus2 className="mr-1 h-4 w-4" />
          새로 만들기
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {personal.isLoading ? (
          <LoadingSkeleton variant="row" count={2} />
        ) : personal.data && personal.data.length > 0 ? (
          personal.data.map((p) => (
            <div
              key={p.id}
              className="flex items-center gap-2 rounded border border-border bg-bg px-3 py-2"
            >
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="flex items-center gap-2">
                  <span className="truncate text-sm font-medium text-fg">{p.name}</span>
                  {p.base_ref ? (
                    <Badge variant="secondary" className="shrink-0 text-[10px]">
                      {p.base_ref} 덮어씀
                    </Badge>
                  ) : (
                    <Badge variant="outline" className="shrink-0 text-[10px]">
                      신규
                    </Badge>
                  )}
                </span>
                <span className="truncate font-mono text-[11px] text-fg-tertiary">
                  변경 {p.updated_at.slice(0, 10)}
                </span>
              </div>
              <Button variant="ghost" size="sm" onClick={() => setEditing(p)}>
                <Pencil className="mr-1 h-3.5 w-3.5" />
                편집
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="text-fg-danger"
                onClick={() => onDelete(p)}
                disabled={del.isPending}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))
        ) : (
          <p className="rounded border border-dashed border-border bg-bg-secondary px-3 py-3 text-xs text-fg-tertiary">
            아직 만든 {meta.title.replace("내 ", "")}이 없습니다. "새로 만들기"로 추가하세요.
          </p>
        )}

        <SystemReference kind={kind} />
      </CardContent>

      {creating ? <PromptDialog kind={kind} onClose={() => setCreating(false)} /> : null}
      {editing ? (
        <PromptDialog kind={kind} existing={editing} onClose={() => setEditing(null)} />
      ) : null}
    </Card>
  );
}

/** 시스템 프롬프트 참고 목록 - 접힌 상태로 시작, 읽기 전용. */
function SystemReference({ kind }: { kind: PromptKind }) {
  const [open, setOpen] = useState(false);
  const system = useListSystemPrompts(open ? kind : undefined);
  const [viewing, setViewing] = useState<{ name: string; content: string } | null>(null);

  return (
    <div className="mt-1 rounded border border-border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-fg-secondary hover:bg-bg-secondary"
      >
        {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        시스템 기본 {kind === "agent" ? "에이전트" : "작성 규칙"} (참고·읽기 전용)
      </button>
      {open ? (
        <div className="flex flex-wrap gap-1.5 border-t border-border px-3 py-2">
          {system.isLoading ? (
            <span className="text-xs text-fg-tertiary">불러오는 중…</span>
          ) : (
            system.data?.map((s) => (
              <button
                key={s.ref}
                type="button"
                onClick={() => setViewing({ name: s.name, content: s.content })}
                className="rounded-sm border border-border bg-bg-secondary px-2 py-1 text-xs text-fg-secondary hover:text-fg"
              >
                {s.name}
              </button>
            ))
          )}
        </div>
      ) : null}
      {viewing ? (
        <Dialog open onOpenChange={(o) => (!o ? setViewing(null) : undefined)}>
          <DialogContent className="max-w-2xl">
            <DialogHeader>
              <DialogTitle>{viewing.name} (시스템·읽기 전용)</DialogTitle>
              <DialogDescription>
                이 내용을 바탕으로 내 것을 만들려면 위 "새로 만들기"에서 같은 이름으로 저장하세요.
              </DialogDescription>
            </DialogHeader>
            <pre className="max-h-[24rem] overflow-auto whitespace-pre-wrap rounded border border-border bg-bg-secondary p-3 text-xs text-fg-secondary">
              {viewing.content}
            </pre>
            <DialogFooter>
              <Button variant="ghost" onClick={() => setViewing(null)}>
                닫기
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      ) : null}
    </div>
  );
}

function PromptDialog({
  kind,
  existing,
  onClose,
}: {
  kind: PromptKind;
  existing?: PersonalPrompt;
  onClose: () => void;
}) {
  const meta = KIND_META[kind];
  const create = useCreatePersonalPrompt();
  const update = useUpdatePersonalPrompt(existing?.id ?? "");
  const [name, setName] = useState(existing?.name ?? "");
  const [content, setContent] = useState(existing?.content ?? "");
  const pending = create.isPending || update.isPending;
  const valid = name.trim() !== "" && content.trim() !== "";

  const save = async () => {
    if (!valid) return;
    try {
      if (existing) {
        await update.mutateAsync({ name: name.trim(), content: content.trim() });
        toast.success(`"${name.trim()}" 저장됨`);
      } else {
        await create.mutateAsync({ kind, name: name.trim(), content: content.trim() });
        toast.success(`"${name.trim()}" 만들어짐`);
      }
      onClose();
    } catch (err) {
      toast.error("저장 실패", { description: errMsg(err, "값을 확인해 주세요.") });
    }
  };

  return (
    <Dialog open onOpenChange={(o) => (!o ? onClose() : undefined)}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>
            {existing ? "편집" : "새로 만들기"} - {meta.title}
          </DialogTitle>
          <DialogDescription>
            {kind === "agent"
              ? "이름은 목차 설계에서 이 분석가를 고를 때 표시됩니다."
              : "이름이 시스템 규칙과 같으면 그 규칙을 덮어씁니다."}
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="prompt-name">이름</Label>
            <Input
              id="prompt-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={meta.placeholder}
              disabled={pending}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="prompt-content">프롬프트 내용</Label>
            <Textarea
              id="prompt-content"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              className="min-h-[240px] font-mono text-sm"
              placeholder={
                kind === "agent"
                  ? "이 분석가의 전문성·관점·작성 지침을 적으세요."
                  : "적용할 문체·서식 규칙을 적으세요."
              }
              disabled={pending}
            />
          </div>
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
