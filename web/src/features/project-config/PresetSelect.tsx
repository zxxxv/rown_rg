import { Controller, useFormContext } from "react-hook-form";
import { type Preset, PresetSchema } from "@/api/types";
import { cn } from "@/lib/utils";
import { PRESET_DESCRIPTION, PRESET_LABEL } from "./presets";
import type { ProjectFormValues } from "./schema";

export interface PresetSelectProps {
  onPresetChange: (preset: Preset) => void;
  disabled?: boolean;
}

const PRESET_ORDER = PresetSchema.options;

export function PresetSelect({ onPresetChange, disabled }: PresetSelectProps) {
  const { control } = useFormContext<ProjectFormValues>();

  return (
    <Controller
      name="config.preset"
      control={control}
      render={({ field }) => (
        <fieldset
          disabled={disabled}
          className="grid grid-cols-1 gap-3 border-0 p-0 sm:grid-cols-2 xl:grid-cols-4"
        >
          <legend className="sr-only">보고서 유형</legend>
          {PRESET_ORDER.map((p) => {
            const checked = field.value === p;
            const inputId = `preset-${p}`;
            return (
              <label
                key={p}
                htmlFor={inputId}
                className={cn(
                  "relative flex cursor-pointer flex-col items-start gap-2 rounded-lg border p-4 text-left transition-colors",
                  "focus-within:ring-2 focus-within:ring-accent focus-within:ring-offset-2",
                  checked
                    ? "border-accent bg-bg-info"
                    : "border-border bg-bg hover:border-border-strong hover:bg-bg-secondary",
                  disabled && "cursor-not-allowed opacity-60",
                )}
              >
                <input
                  id={inputId}
                  type="radio"
                  name={field.name}
                  value={p}
                  checked={checked}
                  disabled={disabled}
                  onChange={() => {
                    field.onChange(p);
                    onPresetChange(p);
                  }}
                  className="sr-only"
                />
                <div className="flex w-full items-center justify-between gap-2">
                  <span className="text-sm font-semibold text-fg">{PRESET_LABEL[p]}</span>
                  <span
                    aria-hidden
                    className={cn(
                      "h-3 w-3 rounded-full border",
                      checked ? "border-accent bg-accent" : "border-border-strong bg-bg",
                    )}
                  />
                </div>
                <p className="text-xs leading-relaxed text-fg-secondary">{PRESET_DESCRIPTION[p]}</p>
              </label>
            );
          })}
        </fieldset>
      )}
    />
  );
}
