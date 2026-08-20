import { useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import {
  type PersonalPrompt,
  type PromptKind,
  type PromptSpecInput,
  useCreatePersonalPrompt,
  useListSystemPrompts,
  useUpdatePersonalPrompt,
} from "@/api/prompts";
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
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { clearPromptDraft, readPromptDraft, usePromptDraftSave } from "./usePromptDraft";

// ─── 프롬프트 편집 다이얼로그(공용) ───
// 프롬프트 관리 화면과 목차 편집기가 같이 쓴다 - 목차를 짜다 관점이 없을 때
// 폼을 떠나 만들고 오면 작성 중이던 목차가 통째로 날아간다(초안 저장이 있어도
// 동선이 끊긴다).

const KIND_META: Record<PromptKind, { title: string; placeholder: string }> = {
  agent: { title: "내 분석 에이전트", placeholder: "예: 우리회사 시장분석가" },
  rule: { title: "내 작성 규칙", placeholder: "예: 사내 문체 규칙" },
};

function errMsg(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

/** 에이전트 프롬프트의 칸 - 백엔드 composer.SECTION_FIELDS와 같은 순서·라벨.
 * 서버가 이 칸들로 프롬프트 한 장을 조합한다(제목 줄·분량 문구는 자동). */
export const SECTION_FIELDS: [string, string, string][] = [
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

export function PromptDialog({
  kind,
  existing,
  onClose,
  onSaved,
}: {
  kind: PromptKind;
  existing?: PersonalPrompt;
  onClose: () => void;
  /** 저장 직후 호출 - 목차 편집기가 새로 만든 에이전트를 그 절에 바로 배정한다 */
  onSaved?: (prompt: PersonalPrompt) => void;
}) {
  const meta = KIND_META[kind];
  const create = useCreatePersonalPrompt();
  const update = useUpdatePersonalPrompt(existing?.id ?? "");
  const system = useListSystemPrompts(kind);
  // 신규 작성만 초안을 복원한다(편집은 서버가 진실). 마운트 시 한 번만 읽는다.
  const [draft] = useState(() => (existing ? null : readPromptDraft(kind)));
  const [restoredDraft, setRestoredDraft] = useState(draft !== null);
  const [name, setName] = useState(existing?.name ?? draft?.name ?? "");
  const [content, setContent] = useState(existing?.content ?? draft?.content ?? "");
  // 에이전트는 칸으로 받아 서버가 한 장으로 조합한다. 21종이 모두 같은 골격
  // (임무·분석 방법론·핵심 산출물)이라 빈 화면에 통째로 쓰게 할 이유가 없다.
  const [sections, setSections] = useState<Record<string, string>>(
    existing?.spec?.sections ?? draft?.sections ?? {},
  );
  const [freeform, setFreeform] = useState(() => {
    if (kind === "rule") return true;
    if (draft) return draft.freeform;
    // 기존 항목은 칸 값이 있을 때만 칸 모드(자유 편집으로 만든 본문을 숨기면 안 된다).
    if (existing) return Object.keys(existing.spec?.sections ?? {}).length === 0;
    // 신규는 칸 모드로 시작 - 시스템 에이전트를 덮어쓸 때와 구조가 갈리면 혼란스럽고
    // (2026-08-13 사용자 지적), 빈 화면에 페르소나를 통째로 쓰라는 건 무리다.
    return false;
  });
  const [cat, setCat] = useState(existing?.cat ?? draft?.cat ?? "");
  const [description, setDescription] = useState(existing?.description ?? draft?.description ?? "");
  // 공개하면 사내 전원의 에이전트 선택 목록에 뜬다(에이전트만). 초안에는 안 싣는다
  // - 저장 안 한 초안이 되살아나며 공개까지 켜져 있으면 본인도 모르게 열린다.
  const [isPublic, setIsPublic] = useState(existing?.is_public ?? false);
  // 빈 칸 = 지정 없음. 시스템 에이전트를 덮어쓸 때 분량까지 건드리면 원본 값
  // (특허분석 2만~6만자 등)이 조용히 깎이므로, 안 적으면 원본을 그대로 승계한다.
  const [minChars, setMinChars] = useState<string>(
    existing?.spec?.min_chars ? String(existing.spec.min_chars) : (draft?.minChars ?? ""),
  );
  const [maxChars, setMaxChars] = useState<string>(
    existing?.spec?.max_chars ? String(existing.spec.max_chars) : (draft?.maxChars ?? ""),
  );
  // 검색 질의 - 입력 칸은 제거됐지만(아래 JSX 주석) 저장이 spec 전체를 교체하므로
  // 기존 값을 보이지 않게 승계해야 한다. 안 하면 편집 저장이 값을 지운다.
  const carriedQueries = existing?.spec?.queries ?? [];
  // 무엇을 덮어쓸지는 만들 때만 정한다(생성 시 확정, 이후 불변).
  const [baseRef, setBaseRef] = useState<string>(existing?.base_ref ?? draft?.baseRef ?? "");
  // 쓰는 동안 자동 저장 - 실수로 닫거나 새로고침해도 다시 열면 이어서 쓴다.
  usePromptDraftSave(kind, !existing, {
    name,
    content,
    sections,
    freeform,
    cat,
    description,
    minChars,
    maxChars,
    baseRef,
  });
  const discardDraft = () => {
    clearPromptDraft(kind);
    setName("");
    setContent("");
    setSections({});
    setFreeform(kind === "rule");
    setCat("");
    setDescription("");
    setMinChars("");
    setMaxChars("");
    setBaseRef("");
    setRestoredDraft(false);
  };
  const pending = create.isPending || update.isPending;
  const hasSections = Object.values(sections).some((v) => v.trim());
  // 서버 규칙(1000~60000자, 최소<최대)과 같은 검증을 저장 전에 한다. 어긋난 채 보내면
  // 422가 나는데, 그 문구로는 어느 칸이 문제인지 알 수 없었다(2026-08-12 QA 보고).
  const volumeError = (() => {
    if (kind !== "agent") return null;
    const hasMin = minChars.trim() !== "";
    const hasMax = maxChars.trim() !== "";
    if (!hasMin && !hasMax) return null;
    if (!hasMin || !hasMax) return "최소·최대를 함께 적거나 둘 다 비워주세요.";
    const lo = Number(minChars);
    const hi = Number(maxChars);
    if (!Number.isInteger(lo) || !Number.isInteger(hi)) return "정수로 적어주세요.";
    if (lo < 1000 || hi > 60000) return "1,000~60,000자 범위로 적어주세요.";
    if (lo >= hi) return "최소는 최대보다 작아야 합니다.";
    return null;
  })();
  // 백엔드 스키마와 같은 글자수 한도(이름 255·분류 100·설명 500). 넘긴 채 보내면
  // 422가 나므로, 입력하는 동안 어느 칸이 얼마나 넘었는지 미리 알린다.
  const overLimit = (value: string, max: number) => {
    const len = value.trim().length;
    return len > max
      ? `최대 ${max.toLocaleString()}자까지 입력할 수 있습니다 (현재 ${len.toLocaleString()}자)`
      : null;
  };
  const nameError = overLimit(name, 255);
  const catError = overLimit(cat, 100);
  const descriptionError = overLimit(description, 500);
  // 본문·칸 합계 상한 - 백엔드 MAX_PROMPT_CHARS와 동일. 이 본문은 절 작성 콜마다
  // system에 통째로 실리므로, 문서 통붙여넣기를 저장 전에 걸러낸다.
  const CONTENT_MAX = 20000;
  const contentError = freeform ? overLimit(content, CONTENT_MAX) : null;
  const sectionsTotal = Object.values(sections).reduce((n, v) => n + v.length, 0);
  const sectionsError =
    !freeform && sectionsTotal > CONTENT_MAX
      ? `칸 내용 합계는 최대 ${CONTENT_MAX.toLocaleString()}자까지 입력할 수 있습니다 (현재 ${sectionsTotal.toLocaleString()}자)`
      : null;
  const valid =
    name.trim() !== "" &&
    (freeform ? content.trim() !== "" : hasSections) &&
    volumeError === null &&
    nameError === null &&
    catError === null &&
    descriptionError === null &&
    contentError === null &&
    sectionsError === null;

  /** 선택한 시스템 항목을 템플릿으로 로드 - 모든 칸을 그 항목 기준으로 덮어쓴다.
   * '빈 칸만 채우기'는 선택을 바꿀 때 이전 선택의 잔재가 섞였다(이름은 STEEP인데
   * 임무는 산업연관분석 - 2026-08-13 확인). 칩은 템플릿 선택기여야 예측 가능하다. */
  const copyFrom = (ref: string) => {
    const found = system.data?.find((x) => x.ref === ref);
    if (!found) return;
    const hasSecs = Object.keys(found.sections).length > 0;
    setContent(found.content);
    setSections(hasSecs ? found.sections : {});
    if (kind === "agent") setFreeform(!hasSecs);
    setMinChars(found.min_chars ? String(found.min_chars) : "");
    setMaxChars(found.max_chars ? String(found.max_chars) : "");
    setName(kind === "agent" ? `${found.name} (내 버전)` : found.name);
    if (kind === "agent") setCat(found.cat ?? "");
    setDescription(found.description ?? "");
  };

  /** "새로 만들기" 선택 - 이전 선택의 내용이 남으면 새 것을 만드는지 알 수 없다. */
  const startBlankForm = () => {
    setName("");
    setContent("");
    setSections({});
    setFreeform(kind === "rule");
    setCat("");
    setDescription("");
    setMinChars("");
    setMaxChars("");
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
    if (kind === "agent") {
      spec.queries = carriedQueries;
    }
    try {
      if (existing) {
        const saved = await update.mutateAsync({
          name: name.trim(),
          content: content.trim(),
          cat: cat.trim() || null,
          description: description.trim() || null,
          spec,
          is_public: kind === "agent" ? isPublic : undefined,
        });
        toast.success(`${name.trim()} 저장됨`);
        onSaved?.(saved);
      } else {
        const created = await create.mutateAsync({
          kind,
          name: name.trim(),
          content: content.trim(),
          base_ref: baseRef || null,
          cat: cat.trim() || null,
          description: description.trim() || null,
          spec,
          is_public: kind === "agent" ? isPublic : undefined,
        });
        clearPromptDraft(kind); // 저장됐으면 초안은 역할이 끝났다
        toast.success(`${name.trim()} 만들어짐`);
        onSaved?.(created);
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
        {restoredDraft ? (
          <div className="flex flex-wrap items-center gap-2 rounded border border-accent/40 bg-bg-info px-3 py-2">
            <p className="text-xs text-fg-secondary">
              작성하던 내용을 복원했습니다. 새로 시작하려면 초안을 비우세요.
            </p>
            <Button type="button" variant="ghost" size="sm" onClick={discardDraft}>
              초안 비우기
            </Button>
          </div>
        ) : null}
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
                  onClick={() => {
                    setBaseRef("");
                    startBlankForm();
                  }}
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
              aria-invalid={nameError !== null}
              className={cn(nameError && "border-fg-danger focus-visible:ring-fg-danger")}
            />
            {nameError ? <p className="text-xs text-fg-danger">{nameError}</p> : null}
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
                  <span
                    className={cn("text-xs", volumeError ? "text-fg-danger" : "text-fg-tertiary")}
                  >
                    {volumeError ??
                      (Number(minChars) > 0 && Number(maxChars) > Number(minChars)
                        ? `A4 ${Math.max(1, Math.floor(Number(minChars) / 1500))}~${Math.max(1, Math.floor(Number(maxChars) / 1500))}페이지`
                        : "")}
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
                  aria-invalid={catError !== null}
                  className={cn(catError && "border-fg-danger focus-visible:ring-fg-danger")}
                />
                {catError ? <p className="text-xs text-fg-danger">{catError}</p> : null}
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
              aria-invalid={descriptionError !== null}
              className={cn(descriptionError && "border-fg-danger focus-visible:ring-fg-danger")}
            />
            {descriptionError ? <p className="text-xs text-fg-danger">{descriptionError}</p> : null}
          </div>
          {/* 검색 질의 입력 칸은 하루 만에 제거(2026-08-20 사용자 판단: 일반 사용자에게
              너무 어렵다). 근거도 갖춰졌다 - 설계 브리프 AI가 배정 에이전트까지 보고
              절마다 질의를 만들므로(brief_ai.search_queries) 이 칸의 한계효용이 낮다.
              spec.queries 자체는 계약으로 유지 - 시스템 카탈로그(방법론 고유 검색어)와
              가져오기 승계가 쓴다. 아래 save()의 보이지 않는 승계가 기존 값을 지킨다. */}
          {kind === "agent" ? (
            <div className="flex items-start gap-3 rounded border border-border bg-bg-secondary p-3">
              <Switch
                id="prompt-public"
                checked={isPublic}
                onCheckedChange={setIsPublic}
                disabled={pending}
              />
              <div className="flex flex-col gap-0.5">
                <Label htmlFor="prompt-public" className="cursor-pointer">
                  공개
                </Label>
                <p className="text-xs text-fg-tertiary">
                  {isPublic
                    ? "사내 모든 사람의 담당 에이전트 목록에 뜹니다. 여기서 고친 내용은 다음 실행부터 반영되고, 이미 돌고 있는 보고서는 시작 시점 내용 그대로 씁니다."
                    : "나만 씁니다. 켜면 사내 모든 사람이 목차의 담당 에이전트로 고를 수 있습니다."}
                </p>
              </div>
            </div>
          ) : null}
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
              {sectionsError ? <p className="text-xs text-fg-danger">{sectionsError}</p> : null}
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
                className={cn(
                  "min-h-[240px] font-mono text-sm",
                  contentError && "border-fg-danger focus-visible:ring-fg-danger",
                )}
                placeholder={
                  kind === "agent"
                    ? "이 분석가의 전문성·관점·작성 지침을 적으세요."
                    : "적용할 문체·서식 규칙을 적으세요."
                }
                disabled={pending}
                aria-invalid={contentError !== null}
              />
              {contentError ? <p className="text-xs text-fg-danger">{contentError}</p> : null}
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
