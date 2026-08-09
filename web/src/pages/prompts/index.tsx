import { ChevronDown, ChevronRight, FilePlus2, Pencil, ScrollText, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import {
  type PersonalPrompt,
  type PromptKind,
  type PromptSpecInput,
  type SystemPrompt,
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
import { cn } from "@/lib/utils";

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

/** 에이전트 프롬프트의 칸 - 백엔드 composer.SECTION_FIELDS와 같은 순서·라벨.
 * 서버가 이 칸들로 프롬프트 한 장을 조합한다(제목 줄·분량 문구는 자동). */
const SECTION_FIELDS: [string, string, string][] = [
  ["mission", "임무", "이 에이전트가 무엇을 하는 전문가인지 - 담당 범위와 산출 목적"],
  [
    "method",
    "분석 방법론",
    "어떤 절차·기준으로 분석하는지 - 번호 목록으로 적으면 본문 구조가 됩니다",
  ],
  ["deliverables", "핵심 산출물", "반드시 만들어야 할 표·그래프·소결 - 줄마다 하나"],
];

/** 작성 규칙이 꽂히는 자리. 시스템 조각과 1:1이고, 고르지 않으면 기존 규칙 뒤에 덧붙는다. */
const RULE_SLOTS = [
  { ref: "agent_source_rules", label: "출처 규칙" },
  { ref: "agent_visual_rules", label: "시각자료 규칙" },
  { ref: "agent_writing_style", label: "문체 규칙" },
];

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
  const system = useListSystemPrompts(kind);
  const [name, setName] = useState(existing?.name ?? "");
  const [content, setContent] = useState(existing?.content ?? "");
  // 에이전트는 칸으로 받아 서버가 한 장으로 조합한다. 21종이 모두 같은 골격
  // (임무·분석 방법론·핵심 산출물)이라 빈 화면에 통째로 쓰게 할 이유가 없다.
  const [sections, setSections] = useState<Record<string, string>>(existing?.spec?.sections ?? {});
  const [freeform, setFreeform] = useState(
    kind === "rule" || Object.keys(existing?.spec?.sections ?? {}).length === 0,
  );
  const [cat, setCat] = useState(existing?.cat ?? "");
  const [description, setDescription] = useState(existing?.description ?? "");
  // 빈 칸 = 지정 없음. 시스템 에이전트를 덮어쓸 때 분량까지 건드리면 원본 값
  // (특허분석 2만~6만자 등)이 조용히 깎이므로, 안 적으면 원본을 그대로 승계한다.
  const [minChars, setMinChars] = useState<string>(
    existing?.spec?.min_chars ? String(existing.spec.min_chars) : "",
  );
  const [maxChars, setMaxChars] = useState<string>(
    existing?.spec?.max_chars ? String(existing.spec.max_chars) : "",
  );
  // 무엇을 덮어쓸지는 만들 때만 정한다(생성 시 확정, 이후 불변).
  const [baseRef, setBaseRef] = useState<string>(existing?.base_ref ?? "");
  const pending = create.isPending || update.isPending;
  const hasSections = Object.values(sections).some((v) => v.trim());
  const valid = name.trim() !== "" && (freeform ? content.trim() !== "" : hasSections);

  /** 시스템 원문을 폼에 채워 넣는다. 빈 칸에서 페르소나를 쓰라는 건 무리다. */
  const copyFrom = (ref: string) => {
    const found = system.data?.find((x) => x.ref === ref);
    if (!found) return;
    setContent(found.content);
    if (Object.keys(found.sections).length > 0) {
      setSections(found.sections);
      setFreeform(false);
    }
    if (found.min_chars && found.max_chars) {
      setMinChars(String(found.min_chars));
      setMaxChars(String(found.max_chars));
    }
    if (!name.trim()) setName(kind === "agent" ? `${found.name} (내 버전)` : found.name);
    if (kind === "agent" && !cat) setCat(found.cat ?? "");
  };

  const baseOptions =
    kind === "rule"
      ? RULE_SLOTS.map((x) => ({ ref: x.ref, label: x.label }))
      : (system.data ?? []).map((x) => ({ ref: x.ref, label: x.name }));

  const save = async () => {
    if (!valid) return;
    const lo = Number(minChars);
    const hi = Number(maxChars);
    const spec: PromptSpecInput = {};
    if (kind === "agent" && lo > 0 && hi > 0) {
      spec.min_chars = lo;
      spec.max_chars = hi;
    }
    // 칸을 쓰면 서버가 그걸로 본문을 조합한다(자유 편집이면 content 원문 그대로).
    if (kind === "agent" && !freeform && hasSections) spec.sections = sections;
    try {
      if (existing) {
        await update.mutateAsync({
          name: name.trim(),
          content: content.trim(),
          cat: cat.trim() || null,
          description: description.trim() || null,
          spec,
        });
        toast.success(`${name.trim()} 저장됨`);
      } else {
        await create.mutateAsync({
          kind,
          name: name.trim(),
          content: content.trim(),
          base_ref: baseRef || null,
          cat: cat.trim() || null,
          description: description.trim() || null,
          spec,
        });
        toast.success(`${name.trim()} 만들어짐`);
      }
      onClose();
    } catch (err) {
      toast.error("저장 실패", { description: errMsg(err, "값을 확인해 주세요.") });
    }
  };

  return (
    <Dialog open onOpenChange={(o) => (!o ? onClose() : undefined)}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {existing ? "편집" : "새로 만들기"} - {meta.title}
          </DialogTitle>
          <DialogDescription>
            {kind === "agent"
              ? "여기서 만든 에이전트는 프로젝트 생성 화면의 목차에서 절에 배정하면 적용됩니다."
              : "작성 규칙은 프로젝트 생성 화면에서 선택해야 그 보고서에 적용됩니다."}
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-3">
          {!existing ? (
            <div className="flex flex-col gap-1.5">
              <Label>{kind === "agent" ? "시스템 에이전트 덮어쓰기" : "대체할 자리"}</Label>
              <p className="text-xs text-fg-tertiary">
                {kind === "agent"
                  ? "고르면 그 에이전트를 내 버전으로 대체하고 원문이 아래에 채워집니다. 비워두면 새 에이전트로 추가됩니다."
                  : "고른 자리의 회사 표준 규칙을 대체하고 원문이 아래에 채워집니다. 비워두면 기존 규칙 뒤에 추가됩니다."}
              </p>
              <div className="flex flex-wrap gap-1.5">
                <button
                  type="button"
                  onClick={() => setBaseRef("")}
                  className={cn(
                    "rounded-full border px-2.5 py-1 text-xs",
                    baseRef === ""
                      ? "border-accent bg-bg-info font-medium text-fg"
                      : "border-border bg-bg text-fg-secondary hover:border-fg-tertiary",
                  )}
                >
                  {kind === "agent" ? "새로 만들기" : "추가 규칙"}
                </button>
                {baseOptions.map((opt) => (
                  <button
                    key={opt.ref}
                    type="button"
                    onClick={() => {
                      setBaseRef(opt.ref);
                      copyFrom(opt.ref);
                    }}
                    className={cn(
                      "rounded-full border px-2.5 py-1 text-xs",
                      baseRef === opt.ref
                        ? "border-accent bg-bg-info font-medium text-fg"
                        : "border-border bg-bg text-fg-secondary hover:border-fg-tertiary",
                    )}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
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
          {kind === "agent" ? (
            <>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="prompt-min">목표 분량 (자)</Label>
                <p className="text-xs text-fg-tertiary">
                  이 에이전트를 배정한 절의 목표 분량이고 분할 작성 파트 수도 여기서 정해집니다.
                  비워두면{" "}
                  {baseRef ? "덮어쓴 에이전트의 원래 분량을 그대로 씁니다" : "기본값을 씁니다"}.
                </p>
                <div className="flex items-center gap-2">
                  <Input
                    id="prompt-min"
                    type="number"
                    min={1000}
                    max={60000}
                    step={1000}
                    value={minChars}
                    onChange={(e) => setMinChars(e.target.value)}
                    placeholder="15000"
                    className="w-32"
                    disabled={pending}
                  />
                  <span className="text-sm text-fg-tertiary">~</span>
                  <Input
                    type="number"
                    min={1000}
                    max={60000}
                    step={1000}
                    value={maxChars}
                    onChange={(e) => setMaxChars(e.target.value)}
                    placeholder="22500"
                    className="w-32"
                    disabled={pending}
                  />
                  <span className="text-xs text-fg-tertiary">
                    {Number(minChars) > 0 && Number(maxChars) > Number(minChars)
                      ? `A4 ${Math.max(1, Math.floor(Number(minChars) / 1500))}~${Math.max(1, Math.floor(Number(maxChars) / 1500))}페이지`
                      : "최소·최대를 함께 적어주세요"}
                  </span>
                </div>
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="prompt-cat">분류</Label>
                <Input
                  id="prompt-cat"
                  value={cat}
                  onChange={(e) => setCat(e.target.value)}
                  placeholder="예: 정책, 시장, 기술"
                  disabled={pending}
                />
              </div>
            </>
          ) : null}
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="prompt-desc">한 줄 설명</Label>
            <Input
              id="prompt-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="목록에서 이 항목을 알아볼 설명"
              disabled={pending}
            />
          </div>
          {kind === "agent" && !freeform ? (
            <div className="flex flex-col gap-3">
              {SECTION_FIELDS.map(([key, label, hint]) => (
                <div key={key} className="flex flex-col gap-1.5">
                  <Label htmlFor={`prompt-${key}`}>{label}</Label>
                  <p className="text-xs text-fg-tertiary">{hint}</p>
                  <Textarea
                    id={`prompt-${key}`}
                    value={sections[key] ?? ""}
                    onChange={(e) => setSections({ ...sections, [key]: e.target.value })}
                    className="min-h-[110px] text-sm"
                    disabled={pending}
                  />
                </div>
              ))}
              <button
                type="button"
                className="self-start text-xs text-fg-tertiary underline"
                onClick={() => setFreeform(true)}
              >
                칸 대신 전체 원문을 직접 쓰기
              </button>
            </div>
          ) : (
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
              {kind === "agent" ? (
                <button
                  type="button"
                  className="self-start text-xs text-fg-tertiary underline"
                  onClick={() => setFreeform(false)}
                >
                  칸(임무·분석 방법론·핵심 산출물)으로 나눠 쓰기
                </button>
              ) : null}
            </div>
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
