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
import { SourceChannels } from "./SourceChannels";
import { ProjectFormSchema, type ProjectFormValues } from "./schema";
import { clearProjectDraft, useProjectDraft } from "./useFormDraft";

export type ProjectConfigFormMode = "create" | "edit";

export interface ProjectConfigFormProps {
  mode: ProjectConfigFormMode;
  defaultValues?: Partial<ProjectFormValues>;
  onSubmit?: (values: ProjectFormValues) => void | Promise<void>;
  onCancel?: () => void;
  submitting?: boolean;
}

const EMPTY_DEFAULTS: ProjectFormValues = {
  title: "",
  topic: "",
  // 기본 프리셋은 백엔드 카탈로그 id 기준(생성 시 그대로 전송 가능한 값)
  config: defaultsForPreset("예비타당성조사"),
};

export function ProjectConfigForm({
  mode,
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

          {/* 필수 결정은 위 5개로 끝. 기능 토글 6종은 제거됨(2026-08-04) -
              출처 태깅·약어집은 파이프라인 기본 동작. 남은 실스위치는 HyDE뿐. */}
          <details className="group rounded border border-border bg-bg">
            <summary className="flex cursor-pointer select-none items-center gap-2 p-4">
              <span className="inline-flex h-6 w-6 items-center justify-center rounded-sm bg-bg-tertiary font-mono text-xs text-fg-secondary">
                +
              </span>
              <span className="text-base font-semibold text-fg">고급 옵션</span>
              <span className="text-xs text-fg-tertiary">
                자료 출처 안내 · 검색 확장(HyDE) - 기본값으로 충분합니다
              </span>
            </summary>
            <div className="flex flex-col gap-6 border-t border-border p-4">
              <div className="flex flex-col gap-3">
                <h3 className="text-sm font-semibold text-fg">자료 출처</h3>
                <SourceChannels />
              </div>
              <div className="flex flex-col gap-3">
                <h3 className="text-sm font-semibold text-fg">검색 품질</h3>
                <HydeToggle />
              </div>
            </div>
          </details>
        </div>

        {isEdit ? (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-4">
            <div className="flex flex-wrap gap-1.5">
              <Badge variant="secondary">프리셋 · {presetLabel(watchedConfig.preset)}</Badge>
              <Badge variant="secondary">
                모델 ·{" "}
                {watchedConfig.model_mode === "economy"
                  ? "절약(Haiku + GPT-mini)"
                  : "표준(Sonnet 4.6)"}
              </Badge>
            </div>
            <div className="flex items-center gap-2">
              {onCancel ? (
                <Button type="button" variant="ghost" onClick={onCancel} disabled={submitting}>
                  취소
                </Button>
              ) : null}
              <Button
                type="button"
                onClick={() => void onClickSubmit()}
                disabled={submitting || isSubmitting}
              >
                {submitting || isSubmitting ? "처리 중…" : "저장"}
              </Button>
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
                  모델 ·{" "}
                  {watchedConfig.model_mode === "economy"
                    ? "절약(Haiku + GPT-mini)"
                    : "표준(Sonnet 4.6)"}
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

/** HyDE 검색 확장 토글 - 실스위치(백엔드 stages._hyde_enabled_for가 소비). */
function HydeToggle() {
  const { control } = useFormContext<ProjectFormValues>();
  return (
    <Controller
      name="config.hyde_enabled"
      control={control}
      render={({ field }) => (
        <label
          htmlFor="hyde-toggle"
          className="flex cursor-pointer items-start gap-3 rounded border border-border bg-bg p-3 transition-colors hover:bg-bg-secondary"
        >
          <Checkbox
            id="hyde-toggle"
            checked={field.value ?? false}
            onCheckedChange={(checked) => field.onChange(checked === true)}
            aria-describedby="hyde-toggle-desc"
          />
          <div className="flex flex-col gap-0.5">
            <Label htmlFor="hyde-toggle" className="cursor-pointer text-sm font-medium text-fg">
              HyDE 검색 확장 (실험적)
            </Label>
            <span id="hyde-toggle-desc" className="text-xs text-fg-tertiary">
              작성 근거 검색 시 질문을 가상 답변 문서로 확장해 의미 검색 재현율을 높입니다. 절마다
              소형 모델 호출이 추가됩니다(비용 소폭 증가).
            </span>
          </div>
        </label>
      )}
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
            AI가 자료를 검색하고 본문을 쓸 때 쓰는 실제 지시문입니다. 무엇을(대상), 어떤
            관점으로(범위·목적), 필요하면 기간·지역까지 문장으로 적을수록 자료 조사가 정확해집니다.
          </p>
        ) : null}
        {errors.topic ? <p className="text-xs text-fg-danger">{errors.topic.message}</p> : null}
      </div>
    </div>
  );
}
