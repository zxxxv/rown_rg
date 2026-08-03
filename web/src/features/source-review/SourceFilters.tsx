import type { Source } from "@/api/types";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

// 출처 종류(gov/academic/media…) 필터는 제거됨 — 목업 시절 분류로,
// 실데이터는 수집 경로(web_search 등)뿐이라 사용자에게 의미가 없었다.
export interface SourceFiltersState {
  minReliability: number;
  yearsBack: number;
}

export const DEFAULT_FILTERS: SourceFiltersState = {
  minReliability: 0,
  yearsBack: 99,
};

const RELIABILITY_OPTIONS = [
  { value: "0", label: "전체" },
  { value: "0.6", label: "보통 이상" },
  { value: "0.9", label: "높음만" },
];

const YEAR_OPTIONS = [
  { value: "99", label: "전체" },
  { value: "1", label: "1년 이내" },
  { value: "3", label: "3년 이내" },
  { value: "5", label: "5년 이내" },
];

export interface SourceFiltersProps {
  value: SourceFiltersState;
  onChange: (next: SourceFiltersState) => void;
}

export function SourceFilters({ value, onChange }: SourceFiltersProps) {
  return (
    <aside className="flex flex-col gap-6 rounded border border-border bg-bg p-4">
      <div className="flex flex-col gap-2">
        <p className="text-xs font-medium text-fg-secondary">신뢰도</p>
        <Select
          value={String(value.minReliability)}
          onValueChange={(v) => onChange({ ...value, minReliability: Number(v) })}
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {RELIABILITY_OPTIONS.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="flex flex-col gap-2">
        <p className="text-xs font-medium text-fg-secondary">발행 연도</p>
        <Select
          value={String(value.yearsBack)}
          onValueChange={(v) => onChange({ ...value, yearsBack: Number(v) })}
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {YEAR_OPTIONS.map((o) => (
              <SelectItem key={o.value} value={o.value}>
                {o.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </aside>
  );
}

export function applySourceFilters(items: Source[], f: SourceFiltersState): Source[] {
  const cutoff = new Date();
  cutoff.setFullYear(cutoff.getFullYear() - f.yearsBack);
  return items.filter((s) => {
    if (s.reliability < f.minReliability) return false;
    if (f.yearsBack < 99 && s.published_at) {
      const pub = new Date(s.published_at);
      if (!Number.isNaN(pub.getTime()) && pub < cutoff) return false;
    }
    return true;
  });
}
