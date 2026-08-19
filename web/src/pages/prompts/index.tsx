import { ChevronDown, ChevronRight, FilePlus2, Pencil, ScrollText, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { type UserPreset, useDeleteUserPreset, usePresets } from "@/api/presets";
import {
  type PersonalPrompt,
  type PromptKind,
  type SystemPrompt,
  useDeletePersonalPrompt,
  useListPersonalPrompts,
  useListSystemPrompts,
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
import { PresetEditorDialog } from "@/features/project-config/PresetEditorDialog";
import { PromptDialog, SECTION_FIELDS } from "@/features/prompts/PromptDialog";
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
        <MyPresetsSection />
      </div>
    </AppShell>
  );
}

/** 내 목차 프리셋 - 보고서 구성(장·절·에이전트 배정)을 미리 만들어 두고
 * 프로젝트 생성 화면의 보고서 유형에서 불러온다(2026-08-12 QA 2번의 확장). */
function MyPresetsSection() {
  const presets = usePresets();
  const del = useDeleteUserPreset();
  const [editing, setEditing] = useState<UserPreset | null>(null);
  const [creating, setCreating] = useState(false);
  const mine = (presets.data ?? []).filter((p) => p.scope === "personal");

  const onDelete = (id: string, name: string) => {
    del.mutate(id, {
      onSuccess: () => toast.success(`"${name}" 삭제됨`),
      onError: (err) =>
        toast.error("삭제 실패", { description: errMsg(err, "다시 시도해 주세요.") }),
    });
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
        <div>
          <CardTitle className="text-base">내 목차 프리셋</CardTitle>
          <CardDescription>
            보고서 구성(장·절·방향·에이전트 배정)을 저장해 두면 프로젝트 생성 화면의 보고서 유형에서
            불러올 수 있습니다. 같은 구성으로 여러 정책을 분석할 때 씁니다.
          </CardDescription>
        </div>
        <Button size="sm" onClick={() => setCreating(true)}>
          <FilePlus2 className="mr-1 h-4 w-4" />
          새로 만들기
        </Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {presets.isLoading ? (
          <LoadingSkeleton variant="row" count={2} />
        ) : mine.length > 0 ? (
          mine.map((p) => (
            <div
              key={p.id}
              className="flex items-center gap-2 rounded border border-border bg-bg px-3 py-2"
            >
              <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                <span className="flex items-center gap-2">
                  <span className="truncate text-sm font-medium text-fg">{p.name}</span>
                  <Badge variant="outline" className="shrink-0 text-[10px]">
                    {p.n_chapters}장 {p.n_sections}절
                  </Badge>
                </span>
                <span className="truncate text-[11px] text-fg-tertiary">{p.desc}</span>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={() =>
                  setEditing({
                    id: p.id.replace(/^u:/, ""),
                    key: p.id,
                    name: p.name,
                    description: p.desc,
                    // 목록에는 공개 여부가 안 실려 온다(카탈로그 표면) - 편집 다이얼로그가
                    // 상세를 다시 읽어 채운다. 여기선 기본값으로 둔다.
                    is_public: false,
                    n_chapters: p.n_chapters,
                    n_sections: p.n_sections,
                    updated_at: p.updated_at ?? "",
                  })
                }
              >
                <Pencil className="mr-1 h-3.5 w-3.5" />
                편집
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="text-fg-danger"
                onClick={() => onDelete(p.id.replace(/^u:/, ""), p.name)}
                disabled={del.isPending}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))
        ) : (
          <p className="rounded border border-dashed border-border bg-bg-secondary px-3 py-3 text-xs text-fg-tertiary">
            아직 저장한 목차 프리셋이 없습니다. "새로 만들기"로 미리 구성하거나, 프로젝트 생성
            화면의 목차 설계에서 "내 프리셋으로 저장"을 누르세요.
          </p>
        )}
      </CardContent>

      {creating ? <PresetEditorDialog onClose={() => setCreating(false)} /> : null}
      {editing ? <PresetEditorDialog existing={editing} onClose={() => setEditing(null)} /> : null}
    </Card>
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
                  {/* 공개 중인지는 목록에서 바로 보여야 한다 - 편집을 열어야만
                      알 수 있으면 열어 둔 걸 잊는다. */}
                  {p.is_public ? <Badge className="shrink-0 text-[10px]">사내 공개</Badge> : null}
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
  const [viewing, setViewing] = useState<SystemPrompt | null>(null);

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
                onClick={() => setViewing(s)}
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
          <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
            <DialogHeader>
              <DialogTitle>{viewing.name} (시스템·읽기 전용)</DialogTitle>
              <DialogDescription>
                {viewing.kind === "agent"
                  ? '고쳐서 쓰려면 "새로 만들기"에서 이 에이전트를 고르세요. 칸이 원문으로 채워집니다.'
                  : '고쳐서 쓰려면 "새로 만들기"에서 이 규칙 자리를 고르세요. 원문이 채워집니다.'}
              </DialogDescription>
            </DialogHeader>
            {/* 편집 폼과 같은 칸 구조로 보여준다 - 원문 덩어리를 그대로 던지면
                무엇을 고칠 수 있는지가 안 보인다(사용자 지적 2026-08-10). */}
            {Object.keys(viewing.sections).length > 0 ? (
              <div className="flex flex-col gap-3">
                {viewing.min_chars && viewing.max_chars ? (
                  <p className="text-xs text-fg-secondary">
                    목표 분량 {viewing.min_chars.toLocaleString()}~
                    {viewing.max_chars.toLocaleString()}자
                  </p>
                ) : null}
                {SECTION_FIELDS.map(([key, label]) =>
                  viewing.sections[key] ? (
                    <div key={key} className="flex flex-col gap-1">
                      <p className="text-xs font-medium text-fg-secondary">{label}</p>
                      <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded border border-border bg-bg-secondary p-3 text-xs text-fg-secondary">
                        {viewing.sections[key]}
                      </pre>
                    </div>
                  ) : null,
                )}
              </div>
            ) : (
              <pre className="max-h-[24rem] overflow-auto whitespace-pre-wrap rounded border border-border bg-bg-secondary p-3 text-xs text-fg-secondary">
                {viewing.content}
              </pre>
            )}
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
