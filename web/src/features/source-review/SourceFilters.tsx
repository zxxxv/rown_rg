import { type Source, type SourceKind, SourceKindSchema } from "@/api/types";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export interface SourceFiltersState {
  kinds: Set<SourceKind>;
  minReliability: number;
  yearsBack: number;
}

export const DEFAULT_FILTERS: SourceFiltersState = {
  kinds: new Set(),
  minReliability: 0,
  yearsBack: 99,
};

const KIND_LABEL: Record<SourceKind, string> = {
  gov: "정부·공공기관",
  academic: "학술",
  media: "언론",
  library: "회사 라이브러리",
  upload: "사용자 업로드",
  web_search: "웹 검색",
};

const KIND_ORDER = SourceKindSchema.options;

const RELIABILITY_OPTIONS = [
  { value: "0", label: "전체" },
  { value: "0.6", label: "0.6 이상" },
  { value: "0.7", label: "0.7 이상" },
  { value: "0.8", label: "0.8 이상" },
  { value: "0.9", label: "0.9 이상" },
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
  const toggleKind = (k: SourceKind) => {
    const next = new Set(value.kinds);
    if (next.has(k)) next.delete(k);
    else next.add(k);
    onChange({ ...value, kinds: next });
  };

  return (
    <aside className="flex flex-col gap-6 rounded border border-border bg-bg p-4">
      <div className="flex flex-col gap-2">
        <p className="text-xs font-medium text-fg-secondary">출처 종류</p>
        <div className="flex flex-col gap-2">
          {KIND_ORDER.map((k) => {
            const id = `flt-kind-${k}`;
            return (
              <div key={k} className="flex items-center gap-2">
                <Checkbox
                  id={id}
                  checked={value.kinds.has(k)}
                  onCheckedChange={() => toggleKind(k)}
                />
                <Label htmlFor={id} className="cursor-pointer text-sm text-fg">
                  {KIND_LABEL[k]}
                </Label>
              </div>
            );
          })}
        </div>
      </div>

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
    if (f.kinds.size > 0 && !f.kinds.has(s.source_kind)) return false;
    if (s.reliability < f.minReliability) return false;
    if (f.yearsBack < 99 && s.published_at) {
      const pub = new Date(s.published_at);
      if (pub < cutoff) return false;
    }
    return true;
  });
}
