import { zodResolver } from "@hookform/resolvers/zod";
import type { ReactNode } from "react";
import { useEffect, useMemo, useRef } from "react";
import { FormProvider, useForm, useFormContext } from "react-hook-form";
import { toast } from "sonner";
import { CostEstimate } from "@/components/data-display/CostEstimate";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { mergeKeepDirty } from "./_merge";
import { AnalyzerPicker } from "./AnalyzerPicker";
import { DepthSelector } from "./DepthSelector";
import { DifferentiatorToggle } from "./DifferentiatorToggle";
import { estimate } from "./estimator";
import { OutputAndNotification } from "./OutputAndNotification";
import { PresetSelect } from "./PresetSelect";
import { defaultsForPreset, presetLabel } from "./presets";
import { SourceTypeCheckboxes } from "./SourceTypeCheckboxes";
import { ProjectFormSchema, type ProjectFormValues } from "./schema";

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

const DEPTH_LABEL = {
  outline_only: "개요만",
  standard: "표준",
  full_report: "보고서 전체",
  deep_dive: "심층 분석",
} as const;

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
    setValue,
    watch,
    formState: { dirtyFields, isSubmitting },
  } = form;
  const isEdit = mode === "edit";

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
  const cost = useMemo(() => estimate(watchedConfig), [watchedConfig]);

  // Critic Agent ON → 일관성 그래프 자동 ON (의존성 검증)
  const autoToggleNotified = useRef(false);
  useEffect(() => {
    if (watchedConfig.enable_critic_agent && !watchedConfig.enable_consistency_graph) {
      setValue("config.enable_consistency_graph", true, { shouldDirty: true });
      if (!autoToggleNotified.current) {
        toast("Critic Agent 활성화로 일관성 그래프도 자동 활성화됨", {
          description: "Critic Agent는 일관성 그래프 결과를 기반으로 검토합니다.",
        });
        autoToggleNotified.current = true;
      }
    }
    if (!watchedConfig.enable_critic_agent) autoToggleNotified.current = false;
  }, [watchedConfig.enable_critic_agent, watchedConfig.enable_consistency_graph, setValue]);

  const onClickSubmit = handleSubmit(async (values) => {
    await onSubmit?.(values);
  });

  const activeDiffCount = [
    watchedConfig.enable_pre_reconciliation,
    watchedConfig.enable_consistency_graph,
    watchedConfig.enable_dual_track_search,
    watchedConfig.enable_source_tagging,
    watchedConfig.enable_critic_agent,
    watchedConfig.enable_glossary,
  ].filter(Boolean).length;

  return (
    <FormProvider {...form}>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="flex flex-col gap-8">
          <Section number={1} title="기본 정보" badge={isEdit ? "수정 불가" : undefined}>
            <BasicInfo readOnly={isEdit} />
          </Section>
          <Section number={2} title="보고서 유형">
            <PresetSelect onPresetChange={onPresetChange} disabled={isEdit} />
          </Section>
          <Section number={3} title="자료 선택">
            <SourceTypeCheckboxes />
          </Section>
          <Section number={4} title="분석 도구">
            <AnalyzerPicker />
          </Section>
          <Section number={5} title="차별화 기능">
            <DifferentiatorToggle />
          </Section>
          <Section number={6} title="작성 깊이" badge={isEdit ? "수정 불가" : undefined}>
            <DepthSelector disabled={isEdit} />
          </Section>
          <Section number={7} title="출력·알림">
            <OutputAndNotification />
          </Section>
        </div>

        <aside className="flex flex-col gap-4 lg:sticky lg:top-6 lg:self-start">
          <CostEstimate {...cost} />
          <div className="flex flex-col gap-2 rounded border border-border bg-bg p-4">
            <p className="text-xs font-medium text-fg-secondary">선택 옵션 요약</p>
            <div className="flex flex-wrap gap-1.5">
              <Badge variant="secondary">프리셋 · {presetLabel(watchedConfig.preset)}</Badge>
              <Badge variant="secondary">깊이 · {DEPTH_LABEL[watchedConfig.depth_mode]}</Badge>
              <Badge variant="secondary">
                분석 도구 {watchedConfig.enabled_analyzers.length}개
              </Badge>
              <Badge variant="secondary">차별화 {activeDiffCount}개</Badge>
              <Badge variant="secondary">출력 {watchedConfig.output_formats.length}개</Badge>
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
              {submitting || isSubmitting ? "처리 중…" : isEdit ? "저장" : "시작"}
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
          {...register("title")}
          readOnly={readOnly}
          aria-invalid={errors.title ? "true" : undefined}
          className={ro}
        />
        {errors.title ? <p className="text-xs text-fg-danger">{errors.title.message}</p> : null}
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="pf-topic">주제</Label>
        <Textarea
          id="pf-topic"
          rows={3}
          {...register("topic")}
          readOnly={readOnly}
          aria-invalid={errors.topic ? "true" : undefined}
          className={ro}
        />
        {errors.topic ? <p className="text-xs text-fg-danger">{errors.topic.message}</p> : null}
      </div>
    </div>
  );
}
