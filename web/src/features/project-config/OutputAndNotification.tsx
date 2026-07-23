import { Controller, useFormContext } from "react-hook-form";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import type { ProjectFormValues } from "./schema";

type Channel = "email" | "naver_works";

// 출력 형식 선택은 UI에서 제거됨 — 산출물은 항상 HWPX(회사 표준)로 렌더되고
// 다운로드는 출력 페이지에서 받는다. config.output_formats는 스키마 기본값
// ["hwpx"]로 유지(기존 프로젝트 호환). email 알림도 UI에서 제거됨(타입만 유지).
const CHANNEL_LABEL: Partial<Record<Channel, string>> = {
  naver_works: "네이버 웍스",
};

const CHANNEL_ORDER: Channel[] = ["naver_works"];

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
                    label={CHANNEL_LABEL[k] ?? k}
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
