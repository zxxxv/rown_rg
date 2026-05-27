import { Controller, useFormContext } from "react-hook-form";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import type { ProjectFormValues } from "./schema";

type OutputFormat = "hwpx" | "pdf" | "markdown";
type Channel = "email" | "naver_works";

const OUTPUT_LABEL: Record<OutputFormat, string> = {
  hwpx: "HWPX (회사 표준)",
  pdf: "PDF",
  markdown: "Markdown (기술 검토용)",
};

const CHANNEL_LABEL: Record<Channel, string> = {
  email: "이메일",
  naver_works: "네이버 웍스",
};

const OUTPUT_ORDER: OutputFormat[] = ["hwpx", "pdf", "markdown"];
const CHANNEL_ORDER: Channel[] = ["email", "naver_works"];

function ToggleRow({
  id,
  label,
  checked,
  onToggle,
  badge,
}: {
  id: string;
  label: string;
  checked: boolean;
  onToggle: (next: boolean) => void;
  badge?: string;
}) {
  return (
    <label
      htmlFor={id}
      className={cn(
        "flex cursor-pointer items-center gap-2 rounded border p-3 transition-colors",
        checked ? "border-accent bg-bg-info" : "border-border bg-bg hover:bg-bg-secondary",
      )}
    >
      <Checkbox id={id} checked={checked} onCheckedChange={(v) => onToggle(v === true)} />
      <Label htmlFor={id} className="cursor-pointer text-sm text-fg">
        {label}
      </Label>
      {badge ? (
        <span className="ml-auto rounded-sm border border-border bg-bg-secondary px-1.5 py-0.5 text-[10px] text-fg-tertiary">
          {badge}
        </span>
      ) : null}
    </label>
  );
}

export function OutputAndNotification() {
  const { control } = useFormContext<ProjectFormValues>();

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      <div className="flex flex-col gap-2">
        <p className="text-xs font-medium text-fg-secondary">출력 형식</p>
        <Controller
          name="config.output_formats"
          control={control}
          render={({ field }) => {
            const selected = new Set(field.value);
            const toggle = (k: OutputFormat) => {
              const next = new Set(selected);
              if (next.has(k)) next.delete(k);
              else next.add(k);
              field.onChange(OUTPUT_ORDER.filter((x) => next.has(x)));
            };
            return (
              <div className="flex flex-col gap-2">
                {OUTPUT_ORDER.map((k) => (
                  <ToggleRow
                    key={k}
                    id={`output-${k}`}
                    label={OUTPUT_LABEL[k]}
                    checked={selected.has(k)}
                    onToggle={() => toggle(k)}
                  />
                ))}
              </div>
            );
          }}
        />
      </div>

      <div className="flex flex-col gap-2">
        <p className="text-xs font-medium text-fg-secondary">알림 채널</p>
        <Controller
          name="config.notification_channels"
          control={control}
          render={({ field }) => {
            const selected = new Set(field.value);
            const toggle = (k: Channel) => {
              const next = new Set(selected);
              if (next.has(k)) next.delete(k);
              else next.add(k);
              field.onChange(CHANNEL_ORDER.filter((x) => next.has(x)));
            };
            return (
              <div className="flex flex-col gap-2">
                {CHANNEL_ORDER.map((k) => (
                  <ToggleRow
                    key={k}
                    id={`channel-${k}`}
                    label={CHANNEL_LABEL[k]}
                    checked={selected.has(k)}
                    onToggle={() => toggle(k)}
                    badge={k === "naver_works" ? "준비 중" : undefined}
                  />
                ))}
              </div>
            );
          }}
        />
      </div>
    </div>
  );
}
