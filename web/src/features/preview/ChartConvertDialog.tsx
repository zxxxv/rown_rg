// 표를 그래프로 바꾸는 대화상자 - 사람이 유형·축·값 열을 고르고 결과를 보고 저장한다.
//
// 미리보기는 본문과 **같은 렌더러**(ChartBlock)로 그린다. 저장 뒤에 다른 그림이 나오면
// 고른 의미가 없다. 저장 단추는 그릴 수 있는 스펙일 때만 열리고, 왜 못 그리는지 적는다.

import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ChartBlock } from "./ChartBlock";
import { type ChartType, MAX_SERIES, toFence, toFenceBody } from "./chartSpec";
import {
  ambiguousCell,
  buildSpec,
  type ConvertChoice,
  defaultChoice,
  hasMixedRowUnits,
  hasMixedUnits,
  type MarkdownTable,
  numericColumns,
} from "./tableToChart";

const TYPE_LABELS: Array<{ value: ChartType; label: string; hint: string }> = [
  { value: "bar", label: "막대", hint: "항목끼리 크기를 견준다" },
  { value: "line", label: "꺾은선", hint: "시간에 따른 변화를 본다" },
  { value: "pie", label: "원형", hint: "합이 전체인 구성비" },
];

export interface ChartConvertDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  table: MarkdownTable;
  /** 변환 대상 블록 원문 - 되돌리기용으로 스펙에 담긴다. */
  block: string;
  /** 저장 - 완성된 차트 펜스를 받아 본문 블록을 갈아 끼운다. */
  onConvert: (fence: string) => void;
  busy: boolean;
}

export function ChartConvertDialog({
  open,
  onOpenChange,
  table,
  block,
  onConvert,
  busy,
}: ChartConvertDialogProps) {
  const [choice, setChoice] = useState<ConvertChoice>(() => defaultChoice(table));
  const numeric = useMemo(() => numericColumns(table), [table]);

  const set = (patch: Partial<ConvertChoice>) => setChoice((prev) => ({ ...prev, ...patch }));

  const pickType = (type: ChartType) => {
    // 원형은 값 열이 하나뿐이다 - 여럿 골라 뒀다면 첫 것만 남긴다.
    set(type === "pie" ? { type, seriesCols: choice.seriesCols.slice(0, 1) } : { type });
  };

  const toggleSeries = (col: number) => {
    if (choice.type === "pie") return set({ seriesCols: [col] });
    const next = choice.seriesCols.includes(col)
      ? choice.seriesCols.filter((c) => c !== col)
      : [...choice.seriesCols, col];
    set({ seriesCols: next });
  };

  const spec = useMemo(() => buildSpec(table, choice, block), [table, choice, block]);
  // 구간·변화값이 든 칸은 첫 숫자만 읽힌다 - 그럴듯하게 틀린 그래프가 되므로 미리 알린다.
  const ambiguous = useMemo(() => ambiguousCell(table, choice), [table, choice]);

  // 못 그리는 이유는 파서가 알지만, 숫자가 아닌 칸만은 어느 항목인지 짚어 준다.
  const badCell = useMemo(() => {
    for (const s of spec.series) {
      const i = s.values.findIndex((v) => Number.isNaN(v));
      if (i >= 0) return `'${spec.x[i]}' 행의 ${s.name} 값이 숫자가 아닙니다`;
    }
    return null;
  }, [spec]);

  const problem =
    choice.seriesCols.length === 0
      ? "값으로 쓸 열을 하나 이상 고르세요"
      : (badCell ??
        (choice.seriesCols.length > MAX_SERIES
          ? `값 열은 ${MAX_SERIES}개까지입니다(색을 돌려쓰지 않습니다)`
          : choice.type === "pie" && spec.x.length > MAX_SERIES
            ? `원형은 조각 ${MAX_SERIES}개까지입니다 - 막대가 낫습니다`
            : spec.x.length < 2
              ? "x축 항목이 2개 미만입니다"
              : null));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>표를 그래프로</DialogTitle>
          <DialogDescription>
            표의 수치를 그대로 씁니다. 원본 표는 그래프 안에 보관돼 언제든 되돌릴 수 있고, 한글
            파일에서 그림을 못 그리면 자동으로 표로 나갑니다.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label>유형</Label>
            <div className="flex flex-wrap gap-2">
              {TYPE_LABELS.map((t) => (
                <Button
                  key={t.value}
                  type="button"
                  size="sm"
                  variant={choice.type === t.value ? "default" : "outline"}
                  onClick={() => pickType(t.value)}
                >
                  {t.label}
                </Button>
              ))}
              <span className="self-center text-xs text-fg-tertiary">
                {TYPE_LABELS.find((t) => t.value === choice.type)?.hint}
              </span>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="chart-x">{choice.type === "pie" ? "조각 이름 열" : "x축 열"}</Label>
              <Select
                value={String(choice.xCol)}
                onValueChange={(v) => {
                  // x축으로 옮긴 열은 값 계열에서 뺀다 - 남겨 두면 목록에 안 보이는 채로
                  // 선택된 상태가 되어, 왜 막히는지 알 수 없는 오류만 뜬다.
                  const xCol = Number(v);
                  set({ xCol, seriesCols: choice.seriesCols.filter((c) => c !== xCol) });
                }}
              >
                <SelectTrigger id="chart-x">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {table.headers.map((h, i) => (
                    <SelectItem key={h || `col-${i}`} value={String(i)}>
                      {h || `${i + 1}번째 열`}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label>{choice.type === "pie" ? "값 열(하나)" : "값 열"}</Label>
              <div className="flex flex-col gap-1.5 rounded border border-border px-3 py-2">
                {table.headers.map((h, i) =>
                  i === choice.xCol ? null : (
                    <div key={h || `val-${i}`} className="flex items-center gap-2">
                      <Checkbox
                        id={`chart-series-${i}`}
                        checked={choice.seriesCols.includes(i)}
                        onCheckedChange={() => toggleSeries(i)}
                      />
                      <Label htmlFor={`chart-series-${i}`} className="cursor-pointer text-xs">
                        {h || `${i + 1}번째 열`}
                      </Label>
                      {numeric.includes(i) ? null : (
                        <span className="text-xs text-fg-warning">수치 열이 아닙니다</span>
                      )}
                    </div>
                  ),
                )}
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="chart-title">제목</Label>
              <Input
                id="chart-title"
                value={choice.title}
                onChange={(e) => set({ title: e.target.value })}
                placeholder="예: 주요국 SMR 누적 투자액"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="chart-unit">단위</Label>
              <Input
                id="chart-unit"
                value={choice.unit}
                onChange={(e) => set({ unit: e.target.value })}
                placeholder="예: 억 원"
                disabled={choice.type === "pie"}
              />
            </div>
          </div>

          {/* 막지는 않는다 - 사람이 알고 고를 수도 있다. 다만 결과를 미리 말해 준다.
              틀린 그래프는 표보다 나쁘므로, 조용히 넘어가는 것만은 하지 않는다. */}
          {hasMixedUnits(table, choice.seriesCols) ? (
            <p className="text-xs text-fg-warning">
              단위가 다른 열을 함께 골랐습니다 - 한 축에 얹히므로 작은 값이 눌려 보입니다.
            </p>
          ) : null}
          {hasMixedRowUnits(table, choice.xCol) ? (
            <p className="text-xs text-fg-warning">
              x축 항목마다 단위가 다릅니다 - 개수와 비율을 한 축에 얹으면 큰 값이 작은 값을 눌러
              뜻이 흐려집니다.
            </p>
          ) : null}
          {ambiguous ? <p className="text-xs text-fg-warning">{ambiguous}</p> : null}

          <div className="rounded border border-border bg-bg-secondary px-3 py-2">
            <p className="mb-1 text-xs font-medium text-fg-secondary">미리보기</p>
            <div className="bg-bg px-3">
              <ChartBlock source={toFenceBody(spec)} />
            </div>
          </div>
        </div>

        <DialogFooter className="items-center">
          {problem ? <p className="mr-auto text-xs text-fg-warning">{problem}</p> : null}
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            취소
          </Button>
          <Button onClick={() => onConvert(toFence(spec))} disabled={busy || problem !== null}>
            {busy ? "저장 중…" : "그래프로 바꾸기"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
