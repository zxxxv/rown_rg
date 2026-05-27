import { Controller, useFormContext } from "react-hook-form";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import type { ProjectFormValues } from "./schema";

interface SourceOption {
  name:
    | "config.sources.use_library"
    | "config.sources.use_upload"
    | "config.sources.use_web_search";
  id: string;
  label: string;
  description: string;
}

const OPTIONS: SourceOption[] = [
  {
    name: "config.sources.use_library",
    id: "source-library",
    label: "회사 자료 라이브러리",
    description: "사내 저장소의 검증된 자료. (실 연동 준비 중)",
  },
  {
    name: "config.sources.use_upload",
    id: "source-upload",
    label: "새 자료 업로드",
    description: "이번 프로젝트에서 직접 올린 PDF·HWPX·DOCX 등.",
  },
  {
    name: "config.sources.use_web_search",
    id: "source-web-search",
    label: "AI 자동 인터넷 검색",
    description: "최신 통계·뉴스·연구자료를 듀얼 트랙 검색으로 수집.",
  },
];

export interface SourceTypeCheckboxesProps {
  disabled?: boolean;
}

export function SourceTypeCheckboxes({ disabled }: SourceTypeCheckboxesProps) {
  const { control } = useFormContext<ProjectFormValues>();

  return (
    <div className="flex flex-col gap-2">
      {OPTIONS.map((opt) => (
        <Controller
          key={opt.name}
          name={opt.name}
          control={control}
          render={({ field }) => (
            <label
              htmlFor={opt.id}
              className={cn(
                "flex cursor-pointer items-start gap-3 rounded border border-border bg-bg p-3 transition-colors hover:bg-bg-secondary",
                disabled && "cursor-not-allowed opacity-60",
              )}
            >
              <Checkbox
                id={opt.id}
                checked={field.value}
                onCheckedChange={(checked) => field.onChange(checked === true)}
                disabled={disabled}
                aria-describedby={`${opt.id}-desc`}
              />
              <div className="flex flex-col gap-0.5">
                <Label htmlFor={opt.id} className="cursor-pointer text-sm font-medium text-fg">
                  {opt.label}
                </Label>
                <span id={`${opt.id}-desc`} className="text-xs text-fg-tertiary">
                  {opt.description}
                </span>
              </div>
            </label>
          )}
        />
      ))}
    </div>
  );
}
