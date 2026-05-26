import type { Preset } from "@/api/types";
import {
  PRESET_DEFAULTS,
  PRESET_DESCRIPTION,
  PRESET_LABEL,
} from "@/features/project-config/presets";

export interface PresetItem {
  preset: Preset;
  label: string;
  description: string;
  defaults: (typeof PRESET_DEFAULTS)[Preset];
}

export const DEMO_PRESETS: PresetItem[] = (Object.keys(PRESET_DEFAULTS) as Preset[]).map((p) => ({
  preset: p,
  label: PRESET_LABEL[p],
  description: PRESET_DESCRIPTION[p],
  defaults: PRESET_DEFAULTS[p],
}));
