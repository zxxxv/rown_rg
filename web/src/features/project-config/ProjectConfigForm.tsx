import { zodResolver } from "@hookform/resolvers/zod";
import type { ReactNode } from "react";
import { Controller, FormProvider, useForm, useFormContext } from "react-hook-form";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { mergeKeepDirty } from "./_merge";
import { ModelModePicker } from "./ModelModePicker";
import { OutlineDesigner } from "./OutlineDesigner";
import { PresetSelect } from "./PresetSelect";
import { defaultsForPreset, presetLabel } from "./presets";
import { RulePicker } from "./RulePicker";
import { ProjectFormSchema, type ProjectFormValues } from "./schema";
import { clearProjectDraft, useProjectDraft } from "./useFormDraft";

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
  const form = useForm<ProjectFormValues>({
    resolver: zodResolver(ProjectFormSchema),
    defaultValues: { ...EMPTY_DEFAULTS, ...defaultValues },
    mode: "onSubmit",
  });
  const {
    handleSubmit,
    getValues,
    reset,
    watch,
    formState: { dirtyFields, isSubmitting },
  } = form;
  const isEdit = mode === "edit";
  // 생성 폼은 순수 메모리 상태라 잠깐 다른 화면에 다녀오거나 새로고침만 해도
  // 목차 편집이 통째로 날아갔다 - 브라우저에 초안을 남긴다(수정 모드는 서버가 진실).
  const draft = useProjectDraft(form, !isEdit);

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

  const onClickSubmit = handleSubmit(async (values) => {
    // 목차는 필수(AI 설계 없음) - 유효한 절이 없으면 생성 자체를 막는다.
    if (!isEdit) {
      const total = values.config.outline?.chapters.reduce((n, c) => n + c.sections.length, 0) ?? 0;
      if (total === 0) {
        toast.error("목차를 구성해 주세요", {
          description: "장·절을 1개 이상 만들어야 시작할 수 있습니다 (3. 목차 설계)",
        });
        return;
      }
    }
    await onSubmit?.(values);
    if (!isEdit) clearProjectDraft(); // 저장됐으면 초안은 역할이 끝났다
  });

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
                작성하던 내용을 복원했습니다. 새로 시작하려면 초안을 비우세요.
              </p>
              <Button type="button" variant="ghost" size="sm" onClick={draft.discard}>
                초안 비우기
              </Button>
            </div>
          ) : !isEdit ? (
            // 자동 저장은 조용히 돌아서 있는 줄 모른다(2026-08-12 QA: 중간저장 요청).
            // 한 줄로 알려 안심하고 이탈·새로고침할 수 있게 한다.
            <p className="text-xs text-fg-tertiary">
              작성 중인 내용은 이 브라우저에 자동 저장됩니다 - 화면을 나갔다 와도 이어서 쓸 수
              있습니다.
            </p>
          ) : null}
          {frozen ? (
            <p className="rounded border border-border bg-bg-secondary px-3 py-2 text-xs text-fg-secondary">
              완성된 보고서라 설정은 읽기 전용입니다. 목차를 바꾸면 이미 쓰인 절과 어긋나므로 고칠
              수 없습니다 - 다른 구성으로 쓰려면 새 프로젝트로 시작하세요.
            </p>
          ) : null}
          <Section number={1} title="기본 정보" badge={isEdit ? "수정 불가" : undefined}>
            <BasicInfo readOnly={isEdit} />
          </Section>
          <Section number={2} title="보고서 유형">
            <PresetSelect onPresetChange={onPresetChange} disabled={isEdit} />
          </Section>
          <Section number={3} title="목차 설계">
            <OutlineDesigner />
          </Section>
          <Section number={4} title="작성 규칙">
            <RulePicker disabled={isEdit} />
          </Section>
          <Section number={5} title="모델 품질" badge={isEdit ? "수정 불가" : undefined}>
            {/* 작성 깊이 UI는 제거 - 분할 생성 배선 이후 깊이는 항상 full_report(2026-08-07) */}
            <ModelModePicker disabled={isEdit} />
          </Section>
          <Section number={6} title="알림">
            <NotificationChannels />
          </Section>
          <Section number={7} title="검색 옵션">
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
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
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
        ) : (
          <aside className="flex flex-col gap-4 lg:sticky lg:top-6 lg:self-start">
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
  children,
}: {
  number: number;
  title: string;
  badge?: string;
  children: ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3">
      <header className="flex items-center gap-2">
        <span className="inline-flex h-6 w-6 items-center justify-center rounded-sm bg-bg-tertiary font-mono text-xs text-fg-secondary">
          {number}
        </span>
        <h2 className="text-base font-semibold text-fg">{title}</h2>
        {badge ? (
          <span className="rounded-sm border border-border bg-bg-secondary px-1.5 py-0.5 text-xs text-fg-tertiary">
            {badge}
          </span>
        ) : null}
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
    formState: { errors },
  } = useFormContext<ProjectFormValues>();
  const ro = cn(readOnly && "bg-bg-secondary text-fg-secondary");
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="pf-title">보고서 제목</Label>
        <Input
          id="pf-title"
          placeholder="예: 2026년 국내 이차전지 산업 동향과 대응 전략"
          {...register("title")}
          readOnly={readOnly}
          aria-invalid={errors.title ? "true" : undefined}
          className={ro}
        />
        {!readOnly ? (
          <p className="text-xs text-fg-tertiary">
            완성된 보고서의 표지 제목이자 프로젝트 이름입니다 - 보고서에 그대로 실립니다.
          </p>
        ) : null}
        {errors.title ? <p className="text-xs text-fg-danger">{errors.title.message}</p> : null}
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="pf-topic">주제</Label>
        <Textarea
          id="pf-topic"
          rows={3}
          placeholder="예: 국내 이차전지 산업의 시장 규모와 최근 3년 추이를 분석하고, 소재·셀·완성차 관점의 공급망 리스크와 향후 5년 대응 전략을 제시"
          {...register("topic")}
          readOnly={readOnly}
          aria-invalid={errors.topic ? "true" : undefined}
          className={ro}
        />
        {!readOnly ? (
          <p className="text-xs text-fg-tertiary">
            자료 수집의 틀입니다. 장마다 도는 웹 수집이 "이 문장 - 장 제목"을 검색 주제로 쓰고,
            설계 검토의 절별 검색 질의 생성과 근거 재채점(주제 이탈 방지)에도 참고됩니다.
            무엇을(대상), 어떤 관점으로(범위·목적) 적을수록 수집이 정확해집니다. 본문 작성은 이
            문장을 보지 않습니다 - 절마다 쓸 내용은 아래 목차의 작성 방향·핵심 포인트에 적어
            주세요.
          </p>
        ) : null}
        {errors.topic ? <p className="text-xs text-fg-danger">{errors.topic.message}</p> : null}
      </div>
    </div>
  );
}
