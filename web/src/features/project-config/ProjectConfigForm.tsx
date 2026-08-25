import { zodResolver } from "@hookform/resolvers/zod";
import { type ReactNode, useCallback, useMemo, useState } from "react";
import { Controller, FormProvider, useForm, useFormContext } from "react-hook-form";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { HelpTip } from "@/components/ui/help-tip";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { mergeKeepDirty } from "./_merge";
import { ModelModePicker } from "./ModelModePicker";
import { OutlineDesigner } from "./OutlineDesigner";
import type { OutlineFocusTarget } from "./OutlineEditor";
import { PresetSelect } from "./PresetSelect";
import { defaultsForPreset, presetLabel } from "./presets";
import { ReadinessPanel } from "./ReadinessPanel";
import { RulePicker } from "./RulePicker";
import { RunFlowGuide } from "./RunFlowGuide";
import { ProjectFormSchema, type ProjectFormValues } from "./schema";
import { clearProjectDraft, readProjectDraft, useProjectDraft } from "./useFormDraft";
import { collectFormIssues, type FormIssue, foldIssues, LIMITS } from "./validation";

export type ProjectConfigFormMode = "create" | "edit";

export interface ProjectConfigFormProps {
  mode: ProjectConfigFormMode;
  /** 완료·보관된 보고서 - 읽기 전용. 저장할 '다음 단계'가 없고, 목차를 바꾸면 절
   * 재작성이 배열 위치로 계획을 되살리므로 다른 절의 계획이 섞인다(서버도 거부한다). */
  frozen?: boolean;
  defaultValues?: Partial<ProjectFormValues>;
  onSubmit?: (values: ProjectFormValues) => void | Promise<void>;
  onCancel?: () => void;
  submitting?: boolean;
}

const MODEL_MODE_LABEL: Record<string, string> = {
  economy: "절약(Haiku + GPT-mini)",
  standard: "표준(Haiku 수집 + Sonnet 작성)",
  premium: "고급(Haiku 수집 + Opus 5 작성)",
};

const EMPTY_DEFAULTS: ProjectFormValues = {
  title: "",
  topic: "",
  // 기본 프리셋은 백엔드 카탈로그 id 기준(생성 시 그대로 전송 가능한 값)
  config: defaultsForPreset("예비타당성조사"),
};

export function ProjectConfigForm({
  mode,
  frozen = false,
  defaultValues,
  onSubmit,
  onCancel,
  submitting,
}: ProjectConfigFormProps) {
  const isEdit = mode === "edit";
  // 초안은 **폼의 초기값**으로 들어가야 한다 - effect로 나중에 넣으면 목차 편집기가
  // 먼저 마운트돼(자식 effect가 먼저 돈다) "목차 없음"으로 보고 프리셋 골격을
  // 예약하고, 그 골격이 도착해 복원된 목차를 덮어썼다(2026-08-25 관통에서 실측:
  // 제목·주제만 살고 장·절은 프리셋 원본으로 되돌아갔다).
  const [initialDraft] = useState(() => (mode === "edit" ? null : readProjectDraft()));
  const form = useForm<ProjectFormValues>({
    resolver: zodResolver(ProjectFormSchema),
    defaultValues: initialDraft?.values ?? { ...EMPTY_DEFAULTS, ...defaultValues },
    mode: "onSubmit",
  });
  const {
    handleSubmit,
    getValues,
    reset,
    watch,
    formState: { dirtyFields, isSubmitting },
  } = form;
  // 생성 폼은 순수 메모리 상태라 잠깐 다른 화면에 다녀오거나 새로고침만 해도
  // 목차 편집이 통째로 날아갔다 - 브라우저에 초안을 남긴다(수정 모드는 서버가 진실).
  const draft = useProjectDraft(form, !isEdit, initialDraft);

  const onPresetChange = (preset: string | null) => {
    const current = getValues();
    const nextConfig = defaultsForPreset(preset);
    const dirtyConfig = (dirtyFields as { config?: unknown }).config;
    const merged = mergeKeepDirty(current.config, nextConfig, dirtyConfig);
    reset(
      { ...current, config: { ...merged, preset } },
      { keepDirty: true, keepDirtyValues: true },
    );
  };

  const watchedConfig = watch("config");
  const watchedTitle = watch("title");
  const watchedTopic = watch("topic");

  // ─── 생성 준비 상태 - 타이핑하는 동안 "무엇이 빠졌는지"를 계속 갱신한다 ───
  // 종전에는 제출을 눌러야 알았고, 목차 쪽 사유(없는 절 참조·절 수 초과·제목 없는
  // 절)는 서버 422 문구가 전부였다(2026-08-25 사용자 지적). 목차 문제는 편집기가
  // 계산해 올려 준다 - config.outline은 이미 정리된 값이라 사라진 줄이 안 보인다.
  const [outlineIssues, setOutlineIssues] = useState<FormIssue[]>([]);
  const [focusTarget, setFocusTarget] = useState<OutlineFocusTarget | null>(null);
  // 편집기의 useEffect 의존성에 들어가므로 신원이 고정돼야 한다(무한 렌더 방지).
  const handleOutlineIssues = useCallback((next: FormIssue[]) => setOutlineIssues(next), []);
  const handleFocusHandled = useCallback(() => setFocusTarget(null), []);

  const issues = useMemo(
    () =>
      collectFormIssues({
        title: watchedTitle ?? "",
        topic: watchedTopic ?? "",
        rules: watchedConfig.rules,
        outlineIssues,
      }),
    [watchedTitle, watchedTopic, watchedConfig.rules, outlineIssues],
  );
  const blockers = useMemo(() => issues.filter((i) => i.level === "blocker"), [issues]);
  const foldedIssues = useMemo(() => foldIssues(issues), [issues]);

  /** 문제 한 줄에서 그 칸으로 - 목차 안이면 장을 펼치는 일까지 편집기가 한다. */
  const jumpTo = useCallback((issue: FormIssue) => {
    if (issue.target.kind === "field") {
      const el = document.getElementById(issue.target.elementId);
      el?.scrollIntoView({ behavior: "smooth", block: "center" });
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
        el.focus({ preventScroll: true });
      }
      return;
    }
    const { chapterIndex, sectionIndex, field } = issue.target;
    // nonce: 같은 자리를 다시 눌러도 편집기가 다시 이동해야 한다.
    setFocusTarget({ chapterIndex, sectionIndex, field, nonce: performance.now() });
  }, []);

  const submitValues = handleSubmit(async (values) => {
    await onSubmit?.(values);
    if (!isEdit) clearProjectDraft(); // 저장됐으면 초안은 역할이 끝났다
  });

  const onClickSubmit = async () => {
    // 막는 사유가 있으면 제출하지 않고 첫 사유로 데려간다 - 사유는 사이드바
    // 체크리스트에 이미 떠 있으므로 토스트는 "몇 개"와 "어디"만 말한다.
    if (blockers.length > 0) {
      const first = blockers[0];
      toast.error(`${first.where} - ${first.message}`, {
        description:
          blockers.length > 1
            ? `고쳐야 할 것이 ${blockers.length}가지입니다. 오른쪽 '생성 준비' 목록을 보세요.`
            : undefined,
        duration: 8000,
      });
      jumpTo(first);
      return;
    }
    await submitValues();
  };

  return (
    <FormProvider {...form}>
      {/* 생성 = 2컬럼(본문+고정 요약 사이드바). 수정(edit)은 개요 아코디언 같은
          좁은 컨테이너에 임베드되므로 단일 컬럼 + 하단 액션 바로 그린다 -
          320px 고정 사이드바가 좁은 폭에서 본문을 짓누르는 문제 방지. */}
      <div
        className={cn("grid grid-cols-1 gap-6", !isEdit && "lg:grid-cols-[minmax(0,1fr)_320px]")}
      >
        <div className="flex flex-col gap-8">
          {draft.restored ? (
            <div className="flex flex-wrap items-center gap-2 rounded border border-accent/40 bg-bg-info px-3 py-2">
              <p className="text-xs text-fg-secondary">
                작성하던 내용을 <span className="font-medium text-fg">목차까지 그대로</span>{" "}
                복원했습니다
                {draft.savedAt ? ` (마지막 저장 ${formatSavedAt(draft.savedAt)})` : ""}. 새로
                시작하려면 초안을 비우세요.
              </p>
              <Button type="button" variant="ghost" size="sm" onClick={draft.discard}>
                초안 비우기
              </Button>
            </div>
          ) : !isEdit ? (
            // 자동 저장은 조용히 돌아서 있는 줄 모른다(2026-08-12 QA: 중간저장 요청).
            // 한 줄로 알려 안심하고 이탈·새로고침할 수 있게 한다.
            <p className="text-xs text-fg-tertiary">
              작성 중인 내용은 제목·주제부터 목차 편집까지 이 브라우저에 자동 저장됩니다. 화면을
              나갔다 오거나 새로고침해도 이어서 쓸 수 있습니다.
            </p>
          ) : null}
          {frozen ? (
            <p className="rounded border border-border bg-bg-secondary px-3 py-2 text-xs text-fg-secondary">
              완성된 보고서라 설정은 읽기 전용입니다. 목차를 바꾸면 이미 쓰인 절과 어긋나므로 고칠
              수 없으니, 다른 구성으로 쓰려면 새 프로젝트로 시작하세요.
            </p>
          ) : null}
          <Section
            number={1}
            title="기본 정보"
            required
            hint="표지에 실릴 제목과, 자료를 찾을 때 쓸 주제 한 문장을 적습니다."
            badge={isEdit ? "수정 불가" : undefined}
          >
            <BasicInfo readOnly={isEdit} />
          </Section>
          <Section
            number={2}
            title="보고서 유형"
            hint="고른 유형의 목차 골격이 아래 3번에 펼쳐집니다. 펼친 뒤에는 마음대로 고칠 수 있습니다."
            helpTitle="보고서 유형은 어떻게 고르나요?"
            help={
              <>
                <p>
                  유형은 <b>목차 골격</b>입니다. 고르면 그 유형이 보통 갖추는 장·절 구성이 아래
                  3번에 그대로 들어오고, 절마다 작성 방향·핵심 포인트·담당 에이전트까지 미리
                  채워집니다.
                </p>
                <p>
                  들어온 뒤에는 전부 고칠 수 있습니다. 절을 지우거나 더하고 순서를 바꿔도 됩니다.
                  원래 골격으로 되돌리려면 목차 머리의 '프리셋 골격으로 리셋'을 누르세요.
                </p>
                <p>
                  맞는 유형이 없으면 <b>자유 주제</b>를 고르고 빈 목차부터 직접 만드세요. 자주 쓰는
                  구성은 '내 프리셋으로 저장'해 두면 다음부터 목록에 나타납니다.
                </p>
              </>
            }
          >
            <PresetSelect onPresetChange={onPresetChange} disabled={isEdit} />
          </Section>
          <Section
            number={3}
            title="목차 설계"
            required
            hint="절 하나가 글 한 꼭지입니다. 여기 적은 순서대로 자료를 모으고 씁니다."
            helpTitle="목차가 왜 중요한가요?"
            help={
              <>
                <p>
                  이 목차는 보고서의 뼈대이자 <b>실행 계획</b>입니다. AI가 목차를 대신 만들지
                  않으며, 여기서 확정한 그대로 실행됩니다.
                </p>
                <p>
                  장 제목은 <b>자료 수집의 단위</b>입니다. 장마다 웹 검색이 돌고, 그 장 제목이 아래
                  절들의 검색어에 함께 들어갑니다.
                </p>
                <p>
                  절 하나가 보고서의 글 한 꼭지입니다. 절을 펼치면 그 절에만 해당하는 작성 방향·핵심
                  포인트·담당 에이전트를 정할 수 있습니다.
                </p>
                <p>절은 최대 40개까지 만들 수 있습니다.</p>
              </>
            }
          >
            <OutlineDesigner
              focusTarget={focusTarget}
              onFocusHandled={handleFocusHandled}
              onIssuesChange={handleOutlineIssues}
            />
          </Section>
          <Section
            number={4}
            title="작성 규칙"
            hint="안 고르면 회사 표준 3종(출처·시각자료·문체)이 그대로 적용됩니다."
            helpTitle="작성 규칙이란?"
            help={
              <>
                <p>
                  보고서 <b>전체</b>에 걸리는 문체·출처 표기·시각자료 규칙입니다. 절마다 다르게
                  적용되지 않고 한 번 골라 고정됩니다.
                </p>
                <p>
                  기본은 회사 표준 3종입니다. 내가 만든 규칙을 고르면 같은
                  자리(출처/시각자료/문체)를 대체하거나, 자리가 없으면 뒤에 덧붙습니다.
                </p>
                <p>규칙은 프롬프트 화면에서 만듭니다.</p>
              </>
            }
          >
            <div id="pf-rules">
              <RulePicker disabled={isEdit} />
            </div>
          </Section>
          <Section
            number={5}
            title="모델 품질"
            hint="사람이 검토할 부담과 비용을 맞바꾸는 선택입니다."
            helpTitle="어느 것을 고를까요?"
            help={
              <>
                <p>
                  <b>표준</b>이 기준입니다. 사람이 한 번 훑어 수치와 표를 확인한다는 전제로 쓰기에
                  적당합니다.
                </p>
                <p>
                  <b>고급</b>은 근거를 더 많이 끌어쓰고 단정이 덜 셉니다. 검토 부담이 가장 적은 대신
                  비용이 가장 높으므로, 납품용이라면 이쪽을 고르세요.
                </p>
                <p>
                  <b>절약</b>은 구조와 흐름만 보는 테스트용입니다. 사실 확인을 전부 사람이 한다는
                  전제로만 쓰세요.
                </p>
                <p>실행을 시작하면 바꿀 수 없습니다. 시작 시점의 선택이 그 보고서에 고정됩니다.</p>
              </>
            }
            badge={isEdit ? "수정 불가" : undefined}
          >
            {/* 작성 깊이 UI는 제거 - 분할 생성 배선 이후 깊이는 항상 full_report(2026-08-07) */}
            <ModelModePicker disabled={isEdit} />
          </Section>
          <Section
            number={6}
            title="알림"
            hint="켜 두면 보고서가 완료되거나 검토 대기·실패 상태가 될 때 소유자에게 알림이 갑니다."
          >
            <NotificationChannels />
          </Section>
          <Section
            number={7}
            title="검색 옵션"
            hint="웹 자료를 어디서 찾을지 정합니다."
            helpTitle="국내·해외 어디를 켤까요?"
            help={
              <>
                <p>
                  국내 제도·통계·정책이 본질인 보고서라면 <b>국내만</b> 켜세요. 해외 자료가 섞이면
                  제도 설명이 흐려집니다.
                </p>
                <p>
                  해외 기술 동향이나 글로벌 시장이 본질이라면 <b>해외</b>를 함께 켜세요. 둘 다 켜면
                  국내와 해외를 모두 찾습니다.
                </p>
                <p>업로드한 자료와 라이브러리 자료는 이 설정과 무관하게 항상 함께 쓰입니다.</p>
              </>
            }
          >
            <SearchScopePicker />
          </Section>

          {/* '고급 옵션'은 제거됨(2026-08-20). 남아 있던 유일한 스위치가 HyDE였는데,
              리허설 단계가 근거 부족 절을 판정해 **그 절에만** HyDE를 자동으로 한 번
              돌리게 되면서(3fbd817) 전역 토글이 할 일이 없어졌다. 오히려 해롭다 -
              켜면 모든 절의 모든 질의에 HyDE가 붙는데, 질의 분화(fff9e98)로 절당 질의가
              1개에서 5~6개가 됐으므로 콜이 절당 6배가 된다. 전역 기본 off는 그대로이고
              실험용 오버라이드는 settings.hyde_enabled로 남는다. */}
        </div>

        {isEdit ? (
          <div className="flex flex-col gap-3 border-t border-border pt-4">
            {frozen || foldedIssues.length === 0 ? null : (
              <ReadinessPanel issues={foldedIssues} onJump={jumpTo} compact />
            )}
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex flex-wrap gap-1.5">
                <Badge variant="secondary">프리셋 · {presetLabel(watchedConfig.preset)}</Badge>
                <Badge variant="secondary">
                  모델 · {MODEL_MODE_LABEL[watchedConfig.model_mode] ?? watchedConfig.model_mode}
                </Badge>
              </div>
              <div className="flex items-center gap-2">
                {onCancel ? (
                  <Button type="button" variant="ghost" onClick={onCancel} disabled={submitting}>
                    취소
                  </Button>
                ) : null}
                {frozen ? null : (
                  <Button
                    type="button"
                    onClick={() => void onClickSubmit()}
                    disabled={submitting || isSubmitting}
                  >
                    {submitting || isSubmitting ? "처리 중…" : "저장"}
                  </Button>
                )}
              </div>
            </div>
          </div>
        ) : (
          <aside className="flex flex-col gap-4 lg:sticky lg:top-6 lg:self-start">
            {/* 무엇이 빠져서 생성이 안 되는지 - 스크롤을 따라다니며 계속 답한다 */}
            <ReadinessPanel issues={foldedIssues} onJump={jumpTo} />
            {/* 누르면 무슨 일이 일어나는지 - 생성 단추 바로 옆이 가장 궁금한 자리다 */}
            <RunFlowGuide />
            <div className="flex flex-col gap-2 rounded border border-border bg-bg p-4">
              <p className="text-xs font-medium text-fg-secondary">선택 옵션 요약</p>
              <div className="flex flex-wrap gap-1.5">
                <Badge variant="secondary">프리셋 · {presetLabel(watchedConfig.preset)}</Badge>
                <Badge variant="secondary">
                  목차 ·{" "}
                  {watchedConfig.outline
                    ? `${watchedConfig.outline.chapters.reduce((n, c) => n + c.sections.length, 0)}절 확정`
                    : "미구성 (필수)"}
                </Badge>
                <Badge variant="secondary">
                  모델 · {MODEL_MODE_LABEL[watchedConfig.model_mode] ?? watchedConfig.model_mode}
                </Badge>
              </div>
            </div>
            <div className="flex flex-col gap-2">
              <Button
                type="button"
                size="lg"
                className="w-full"
                onClick={() => void onClickSubmit()}
                disabled={submitting || isSubmitting}
              >
                {submitting || isSubmitting ? "처리 중…" : "프로젝트 생성"}
              </Button>
              {/* 버튼은 잠그지 않는다 - 잠긴 버튼은 이유를 말하지 않는다. 누르면
                  첫 사유로 데려간다(위 목록과 같은 순서). */}
              {blockers.length > 0 ? (
                <p className="text-center text-[11px] text-fg-danger">
                  아직 {blockers.length}가지가 빠졌습니다. 누르면 그 자리로 이동합니다
                </p>
              ) : null}
              {onCancel ? (
                <Button
                  type="button"
                  variant="ghost"
                  className="w-full"
                  onClick={onCancel}
                  disabled={submitting}
                >
                  취소
                </Button>
              ) : null}
            </div>
          </aside>
        )}
      </div>
    </FormProvider>
  );
}

function Section({
  number,
  title,
  badge,
  hint,
  help,
  helpTitle,
  required,
  children,
}: {
  number: number;
  title: string;
  badge?: string;
  /** 이 영역이 결과에서 무슨 일을 하는지 한 줄 - 칸만 있고 설명이 없으면 무엇을 적는 자리인지 알 수 없다 */
  hint?: string;
  /** 자세한 설명 - 물음표를 눌렀을 때만 뜬다(화면은 한 줄로 유지) */
  help?: ReactNode;
  /** 도움말 팝업 머리 - 없으면 영역 제목을 쓴다 */
  helpTitle?: string;
  /** 비우면 생성이 막히는 영역 */
  required?: boolean;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <header className="flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <span className="inline-flex h-6 w-6 items-center justify-center rounded-sm bg-bg-tertiary font-mono text-xs text-fg-secondary">
            {number}
          </span>
          <h2 className="text-base font-semibold text-fg">{title}</h2>
          {required ? (
            <span className="rounded-sm bg-bg-danger px-1.5 py-0.5 text-xs font-medium text-fg-danger">
              필수
            </span>
          ) : (
            <span className="rounded-sm bg-bg-secondary px-1.5 py-0.5 text-xs text-fg-tertiary">
              선택
            </span>
          )}
          {badge ? (
            <span className="rounded-sm border border-border bg-bg-secondary px-1.5 py-0.5 text-xs text-fg-tertiary">
              {badge}
            </span>
          ) : null}
          {help ? (
            <HelpTip title={helpTitle ?? title} size="sm">
              {help}
            </HelpTip>
          ) : null}
        </div>
        {hint ? <p className="text-xs leading-relaxed text-fg-secondary">{hint}</p> : null}
      </header>
      {children}
    </section>
  );
}

/** 완료 알림 채널 - 켜면 보고서 완료·검토대기·실패 시 소유자에게 네이버웍스 봇 알림(기본 off). */
function NotificationChannels() {
  const { control } = useFormContext<ProjectFormValues>();
  return (
    <Controller
      name="config.notification_channels"
      control={control}
      render={({ field }) => {
        const checked = (field.value ?? []).includes("naver_works");
        return (
          <label
            htmlFor="notify-naver-works"
            className="flex w-full cursor-pointer items-center gap-3 rounded border border-border bg-bg p-3 transition-colors hover:bg-bg-secondary sm:max-w-md"
          >
            <Checkbox
              id="notify-naver-works"
              checked={checked}
              onCheckedChange={(v) => field.onChange(v === true ? ["naver_works"] : [])}
            />
            <div className="flex flex-col gap-0.5">
              <Label
                htmlFor="notify-naver-works"
                className="cursor-pointer text-sm font-medium text-fg"
              >
                네이버 웍스
              </Label>
              <span className="text-xs text-fg-tertiary">
                보고서 완성 시 네이버 웍스로 알림을 받습니다.
              </span>
            </div>
          </label>
        );
      }}
    />
  );
}

// 국내 제도·통계가 본질인 보고서와 해외 기술 동향이 본질인 보고서는 정답이 다르다 -
// 한쪽으로 고정하지 않고 프로젝트마다 고르게 한다(2026-08-11).
// 체크박스 다중 선택(2026-08-13 사용자 결정): 국내만/해외만/둘 다. '반반' 프리셋은
// 폐지 - 비율 지시보다 '둘 다 본다'가 실사용 의도였다. 기본은 국내.
const SCOPE_REGIONS = [
  { key: "domestic" as const, label: "국내", hint: "정부·공공기관·국책연구기관·주요 언론" },
  { key: "global" as const, label: "해외", hint: "주요국 기관·국제기구·해외 학술지" },
];

function SearchScopePicker() {
  const { control } = useFormContext<ProjectFormValues>();
  return (
    <Controller
      name="config.search_scope"
      control={control}
      render={({ field }) => {
        const value = field.value ?? "domestic";
        const both = value === "all" || value === "balanced"; // balanced=개편 전 저장값
        const checked = {
          domestic: both || value === "domestic",
          global: both || value === "global",
        };
        const toggle = (key: "domestic" | "global", on: boolean) => {
          const next = { ...checked, [key]: on };
          if (!next.domestic && !next.global) return; // 최소 하나는 남긴다
          field.onChange(
            next.domestic && next.global ? "all" : next.domestic ? "domestic" : "global",
          );
        };
        return (
          <div className="flex flex-wrap gap-2">
            {SCOPE_REGIONS.map((opt) => (
              <label
                key={opt.key}
                htmlFor={`search-scope-${opt.key}`}
                className={cn(
                  "flex cursor-pointer items-start gap-3 rounded border px-3 py-2 transition-colors",
                  checked[opt.key]
                    ? "border-accent bg-bg-info"
                    : "border-border bg-bg hover:border-fg-tertiary",
                )}
              >
                <Checkbox
                  id={`search-scope-${opt.key}`}
                  checked={checked[opt.key]}
                  onCheckedChange={(v) => toggle(opt.key, v === true)}
                  className="mt-0.5"
                />
                <div className="flex flex-col gap-0.5">
                  <span className="text-sm font-medium text-fg">{opt.label}</span>
                  <span className="text-xs text-fg-tertiary">{opt.hint}</span>
                </div>
              </label>
            ))}
          </div>
        );
      }}
    />
  );
}

function BasicInfo({ readOnly }: { readOnly?: boolean }) {
  const {
    register,
    watch,
    formState: { errors, touchedFields, isSubmitted },
  } = useFormContext<ProjectFormValues>();
  const ro = cn(readOnly && "bg-bg-secondary text-fg-secondary");
  // 비었는지·상한을 넘겼는지를 그 자리에서 빨갛게 - 제출을 눌러야 아는 건 늦다.
  const titleLen = (watch("title") ?? "").length;
  const topicLen = (watch("topic") ?? "").length;
  // 손대기 전에는 빨갛게 하지 않는다 - 새 프로젝트 화면을 열자마자 빈 칸이 붉으면
  // 안내가 아니라 지적으로 읽힌다(2026-08-25 전수 조사). 못 채운 사실 자체는 오른쪽
  // '생성 준비' 목록이 처음부터 말하고 있다.
  const titleBad = !readOnly && titleLen === 0 && (touchedFields.title === true || isSubmitted);
  const topicBad = !readOnly && topicLen === 0 && (touchedFields.topic === true || isSubmitted);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between gap-2">
          <Label htmlFor="pf-title">
            보고서 제목
            {readOnly ? null : <span className="ml-1 text-fg-danger">*</span>}
          </Label>
          <CharCount value={titleLen} max={LIMITS.title} hidden={readOnly} />
        </div>
        <Input
          id="pf-title"
          placeholder="예: 2026년 국내 이차전지 산업 동향과 대응 전략"
          maxLength={LIMITS.title}
          {...register("title")}
          readOnly={readOnly}
          aria-invalid={errors.title || titleBad ? "true" : undefined}
          className={cn(ro, titleBad && "border-fg-danger focus-visible:ring-fg-danger")}
        />
        {!readOnly ? (
          <p className="text-xs text-fg-tertiary">
            완성된 보고서의 표지 제목이자 프로젝트 이름이며, 보고서에 그대로 실립니다.
          </p>
        ) : null}
        {titleBad ? (
          <p className="text-xs text-fg-danger">채워야 프로젝트를 만들 수 있습니다.</p>
        ) : null}
        {errors.title ? <p className="text-xs text-fg-danger">{errors.title.message}</p> : null}
      </div>
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between gap-2">
          <Label htmlFor="pf-topic" className="flex items-center gap-1">
            주제
            {readOnly ? null : <span className="text-fg-danger">*</span>}
            <HelpTip title="주제 문장은 어디에 쓰이나요?">
              <p>
                이 문장은 <b>자료를 찾는 데</b> 쓰입니다. 장마다 도는 웹 검색이 "이 문장 + 장
                제목"을 검색어로 쓰고, 찾은 자료가 주제에서 벗어나지 않았는지 가릴 때도 기준이
                됩니다.
              </p>
              <p>
                <b>본문을 쓸 때는 이 문장을 보지 않습니다.</b> 절마다 무엇을 쓸지는 아래 목차의 작성
                방향·핵심 포인트에 적어 주세요.
              </p>
              <p>
                대상과 범위가 구체적일수록 자료가 정확해집니다. 예: "이차전지"(너무 넓음) → "국내
                이차전지 공급망 리스크와 향후 5년 대응 전략"
              </p>
            </HelpTip>
          </Label>
          <CharCount value={topicLen} max={LIMITS.topic} hidden={readOnly} />
        </div>
        <Textarea
          id="pf-topic"
          rows={3}
          placeholder="예: 국내 이차전지 산업의 시장 규모와 최근 3년 추이를 분석하고, 소재·셀·완성차 관점의 공급망 리스크와 향후 5년 대응 전략을 제시"
          maxLength={LIMITS.topic}
          {...register("topic")}
          readOnly={readOnly}
          aria-invalid={errors.topic || topicBad ? "true" : undefined}
          className={cn(ro, topicBad && "border-fg-danger focus-visible:ring-fg-danger")}
        />
        {!readOnly ? (
          <p className="text-xs text-fg-tertiary">
            무엇을(대상), 어떤 관점으로(범위·목적) 볼지 한 문장으로 적으세요.
          </p>
        ) : null}
        {topicBad ? (
          <p className="text-xs text-fg-danger">
            무엇을 어떤 관점으로 볼지 적어야 자료를 찾을 수 있습니다.
          </p>
        ) : null}
        {errors.topic ? <p className="text-xs text-fg-danger">{errors.topic.message}</p> : null}
      </div>
    </div>
  );
}

/** 초안 저장 시각 - 저장은 UTC(ISO), 보이는 것은 이 브라우저의 지역 시간.
 * 오늘이면 시각만, 아니면 날짜까지(어제 것을 오늘 것으로 착각하면 안 된다). */
function formatSavedAt(iso: string): string {
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const sameDay = at.toDateString() === new Date().toDateString();
  return at.toLocaleString("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    ...(sameDay ? {} : { month: "numeric", day: "numeric" }),
  });
}

/** 글자 수 - 상한이 가까워질 때만 나타난다(평소엔 숫자가 시야를 어지럽힌다). */
function CharCount({ value, max, hidden }: { value: number; max: number; hidden?: boolean }) {
  if (hidden || value < max * 0.8) return null;
  return (
    <span className={cn("font-mono text-xs", value >= max ? "text-fg-danger" : "text-fg-tertiary")}>
      {value}/{max}
    </span>
  );
}
