import { useMemo, useState } from "react";
import {
  type ChartSpec,
  ChartSpecError,
  formatValue,
  labelInk,
  parseChartSpec,
  SERIES_COLORS,
} from "./chartSpec";

// 그림 좌표계 - viewBox 안에서 그리고 CSS로 폭에 맞춘다(반응형).
const W = 640;
const H = 320;
// 위 여백은 값 라벨과 "(단위: …)" 두 줄이 겹치지 않을 만큼 둔다.
const PAD = { top: 34, right: 20, bottom: 40, left: 52 };
const PLOT_W = W - PAD.left - PAD.right;
const PLOT_H = H - PAD.top - PAD.bottom;

const GRID = "var(--chart-grid, #e3e3e0)";
const INK = "var(--chart-ink, #52514e)";
// 10px 글자 한 자의 대략 폭(px) - 값 라벨이 막대에 들어가는지 가늠하는 데만 쓴다.
const LABEL_CHAR_W = 5.6;
// x축 라벨(11px)의 한글 한 자 폭 - 한글은 전각이라 숫자·영문보다 넓게 잡는다.
const X_LABEL_CHAR_W = 11;

interface HoverPoint {
  x: number;
  y: number;
  label: string;
  value: number;
  series: string;
  color: string;
}

/** 눈금 값 - 데이터를 **감싸는** 범위를 사람이 읽기 좋은 간격으로. 0은 늘 포함한다.
 *
 * 마지막 눈금이 최대값보다 낮으면 안 된다. 축이 데이터보다 짧으면 막대와 선이 그림
 * 영역을 넘어가 잘리는데(1,419가 눈금 1,000에서 끝난 축을 뚫고 나가 선이 통째로
 * 사라졌다), SVG는 넘친 자리를 그냥 자를 뿐 아무 표시도 하지 않는다.
 *
 * 아래쪽도 같다. 음수를 안 받던 때는 "수입액 △934"짜리 막대가 높이 0으로 그려져
 * 아예 안 보였다(2026-08-28). 감액·감소율 표가 자동 변환으로 들어오면서 실제 값이 됐다. */
function ticks(min: number, max: number): number[] {
  const lo = Math.min(min, 0);
  const hi = Math.max(max, 0);
  if (hi === lo) return [0];
  const raw = (hi - lo) / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? mag * 10;
  // 부동소수 오차로 눈금이 하나 더 붙는 것을 막으려 아주 작은 값을 빼고 올린다.
  const from = Math.floor(lo / step + 1e-9);
  const to = Math.ceil(hi / step - 1e-9);
  return Array.from({ length: to - from + 1 }, (_, i) => {
    const v = (from + i) * step;
    // 0.1 * 3 같은 부동소수 찌꺼기가 눈금 글자에 새지 않게 간격 자릿수로 맞춘다.
    return Number(v.toFixed(Math.max(0, -Math.floor(Math.log10(step)) + 1)));
  });
}

function useScale(spec: ChartSpec) {
  return useMemo(() => {
    const all = spec.series.flatMap((s) => s.values);
    const tickValues = ticks(Math.min(...all, 0), Math.max(...all, 0));
    const top = tickValues[tickValues.length - 1];
    const bottom = tickValues[0];
    // 양수만이면 bottom이 0이라 0을 바닥으로 삼는 것과 같다 - 밑을 자르면 증가폭이
    // 실제보다 커 보인다. 음수가 섞이면 축이 0 아래로 내려가고 0선이 기준이 된다.
    const span = top - bottom || 1;
    const y = (v: number) => PAD.top + PLOT_H - ((v - bottom) / span) * PLOT_H;
    return { top, bottom, tickValues, y, zeroY: y(0) };
  }, [spec]);
}

function Axes({ scale }: { scale: ReturnType<typeof useScale> }) {
  return (
    <g>
      {scale.tickValues.map((v) => (
        <g key={v}>
          {/* 0선은 음수가 섞일 때 기준선이라 격자보다 진하게 - 어디가 0인지 안 보이면
              아래로 자란 막대를 읽을 수 없다. */}
          <line
            x1={PAD.left}
            x2={PAD.left + PLOT_W}
            y1={scale.y(v)}
            y2={scale.y(v)}
            stroke={v === 0 && scale.bottom < 0 ? INK : GRID}
            strokeWidth={1}
          />
          <text x={PAD.left - 8} y={scale.y(v) + 4} textAnchor="end" fontSize={11} fill={INK}>
            {formatValue(v)}
          </text>
        </g>
      ))}
    </g>
  );
}

function XLabels({ spec }: { spec: ChartSpec }) {
  const band = PLOT_W / spec.x.length;
  // 칸 폭에 안 들어가는 라벨은 줄여서 찍는다 - 실제 보고서 항목명은 "2023년 글로벌 배터리
  // 수요"처럼 길어서, 그대로 두면 옆 라벨과 겹쳐 둘 다 못 읽는다. 전체 문구는 <title>로 남긴다.
  const maxChars = Math.max(Math.floor(band / X_LABEL_CHAR_W), 3);
  return (
    <g>
      {spec.x.map((label, i) => (
        <text
          key={label}
          x={PAD.left + band * (i + 0.5)}
          y={PAD.top + PLOT_H + 20}
          textAnchor="middle"
          fontSize={11}
          fill={INK}
        >
          {label.length > maxChars ? `${label.slice(0, maxChars - 1)}…` : label}
          <title>{label}</title>
        </text>
      ))}
    </g>
  );
}

function BarMarks({
  spec,
  scale,
  onHover,
}: {
  spec: ChartSpec;
  scale: ReturnType<typeof useScale>;
  onHover: (p: HoverPoint | null) => void;
}) {
  const band = PLOT_W / spec.x.length;
  const n = spec.series.length;
  const barW = (band * 0.7) / n;
  // 값 라벨은 낮은 색 대비를 메우는 장치라 되도록 찍되, 막대보다 넓으면 이웃과 겹쳐
  // 오히려 안 읽힌다 - 가장 긴 라벨이 막대 폭에 들어갈 때만 찍는다(계열 수가 아니라 폭이 기준).
  const widestLabel = Math.max(
    ...spec.series.flatMap((s) => s.values.map((v) => formatValue(v).length)),
  );
  const showValues = barW >= widestLabel * LABEL_CHAR_W + 2;
  return (
    <g>
      {spec.series.map((s, si) =>
        s.values.map((v, i) => {
          const x = PAD.left + band * (i + 0.5) - (barW * n) / 2 + barW * si;
          // 막대는 0선에서 자란다 - 음수는 아래로. 높이를 바닥에서 재면 음수 막대가
          // 높이 0이 되어 통째로 사라진다.
          const y = Math.min(scale.y(v), scale.zeroY);
          const barH = Math.max(Math.abs(scale.y(v) - scale.zeroY), 1);
          const color = SERIES_COLORS[si];
          // 값 라벨은 막대 바깥이 기본이되, 그림 영역을 벗어날 자리면 막대 안으로 넣는다.
          // 축 끝까지 닿은 막대(-966)의 라벨이 밖으로 나가면 x축 항목명 위에 겹쳐 앉아
          // 둘 다 못 읽는다(2026-08-28 렌더 확인).
          const outsideY = v < 0 ? y + barH + 12 : y - 5;
          const fitsOutside = v < 0 ? outsideY <= PAD.top + PLOT_H : outsideY >= PAD.top + 10;
          const labelY = fitsOutside ? outsideY : v < 0 ? y + barH - 5 : y + 13;
          return (
            <g key={`${s.name}-${spec.x[i]}`}>
              {/* biome-ignore lint/a11y/noStaticElementInteractions: 값은 라벨·범례로 읽힌다 */}
              <rect
                x={x + 1} // 이웃 막대 사이 2px 틈
                y={y}
                width={Math.max(barW - 2, 1)}
                height={barH}
                rx={3}
                fill={color}
                onMouseEnter={() =>
                  onHover({ x: x + barW / 2, y, label: spec.x[i], value: v, series: s.name, color })
                }
                onMouseLeave={() => onHover(null)}
              />
              {showValues && (
                <text
                  x={x + barW / 2}
                  y={labelY}
                  textAnchor="middle"
                  fontSize={10}
                  fill={fitsOutside ? INK : labelInk(color)}
                >
                  {formatValue(v)}
                </text>
              )}
            </g>
          );
        }),
      )}
    </g>
  );
}

function LineMarks({
  spec,
  scale,
  onHover,
}: {
  spec: ChartSpec;
  scale: ReturnType<typeof useScale>;
  onHover: (p: HoverPoint | null) => void;
}) {
  const band = PLOT_W / spec.x.length;
  const px = (i: number) => PAD.left + band * (i + 0.5);
  return (
    <g>
      {spec.series.map((s, si) => {
        const color = SERIES_COLORS[si];
        const d = s.values.map((v, i) => `${i === 0 ? "M" : "L"}${px(i)},${scale.y(v)}`).join(" ");
        return (
          <g key={s.name}>
            <path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" />
            {s.values.map((v, i) => (
              // 화살표 함수의 반환 자리라 {/* */}가 아니라 줄 주석이어야 파싱된다.
              // biome-ignore lint/a11y/noStaticElementInteractions: 호버 툴팁은 보조 정보 - 끝점 라벨·범례로 값이 읽힌다
              <circle
                key={spec.x[i]}
                cx={px(i)}
                cy={scale.y(v)}
                r={4}
                fill={color}
                stroke="white"
                strokeWidth={2}
                onMouseEnter={() =>
                  onHover({
                    x: px(i),
                    y: scale.y(v),
                    label: spec.x[i],
                    value: v,
                    series: s.name,
                    color,
                  })
                }
                onMouseLeave={() => onHover(null)}
              />
            ))}
            {/* 끝점만 직접 라벨 - 모든 점에 숫자를 찍으면 선이 안 보인다. */}
            <text
              x={px(s.values.length - 1) + 6}
              y={scale.y(s.values[s.values.length - 1]) - 6}
              fontSize={10}
              fill={INK}
            >
              {formatValue(s.values[s.values.length - 1])}
            </text>
          </g>
        );
      })}
    </g>
  );
}

function PieMarks({ spec, onHover }: { spec: ChartSpec; onHover: (p: HoverPoint | null) => void }) {
  const values = spec.series[0].values;
  const total = values.reduce((a, b) => a + b, 0) || 1;
  const cx = W / 2;
  const cy = PAD.top + PLOT_H / 2;
  // 밖으로 빠지는 라벨 자리를 반지름에서 미리 뺀다 - 원을 키우면 라벨이 그림 밖에서 잘린다.
  const r = Math.min(PLOT_H, PLOT_W) / 2 - 18;
  let angle = -Math.PI / 2;
  return (
    <g>
      {values.map((v, i) => {
        const sweep = (v / total) * Math.PI * 2;
        const [x0, y0] = [cx + r * Math.cos(angle), cy + r * Math.sin(angle)];
        angle += sweep;
        const [x1, y1] = [cx + r * Math.cos(angle), cy + r * Math.sin(angle)];
        const mid = angle - sweep / 2;
        const color = SERIES_COLORS[i];
        const percent = `${((v / total) * 100).toFixed(1)}%`;
        // 좁은 조각에는 글자가 안 들어간다 - 조각 밖으로 빼서 잘리지 않게 한다.
        const inside = sweep * r * 0.65 >= percent.length * 6;
        const lr = inside ? 0.65 : 1.12;
        return (
          <g key={spec.x[i]}>
            {/* biome-ignore lint/a11y/noStaticElementInteractions: 비율은 직접 라벨로 붙는다 */}
            <path
              d={`M${cx},${cy} L${x0},${y0} A${r},${r} 0 ${sweep > Math.PI ? 1 : 0} 1 ${x1},${y1} Z`}
              fill={color}
              stroke="white"
              strokeWidth={2}
              onMouseEnter={() =>
                onHover({
                  x: cx + (r / 2) * Math.cos(mid),
                  y: cy + (r / 2) * Math.sin(mid),
                  label: spec.x[i],
                  value: v,
                  series: spec.series[0].name,
                  color,
                })
              }
              onMouseLeave={() => onHover(null)}
            />
            <text
              x={cx + r * lr * Math.cos(mid)}
              y={cy + r * lr * Math.sin(mid) + 4}
              textAnchor="middle"
              fontSize={11}
              fill={inside ? labelInk(color) : INK}
              fontWeight={600}
            >
              {percent}
            </text>
          </g>
        );
      })}
    </g>
  );
}

function Legend({ spec }: { spec: ChartSpec }) {
  // 원형은 조각마다 직접 라벨이 붙으므로 x축 항목이 곧 범례다.
  const items =
    spec.type === "pie"
      ? spec.x.map((name, i) => ({ name, color: SERIES_COLORS[i] }))
      : spec.series.map((s, i) => ({ name: s.name, color: SERIES_COLORS[i] }));
  return (
    <div className="mt-1 flex flex-wrap justify-center gap-x-4 gap-y-1">
      {items.map((item) => (
        <span key={item.name} className="flex items-center gap-1.5 text-xs text-fg-secondary">
          <span
            className="inline-block h-2.5 w-2.5 rounded-sm"
            style={{ backgroundColor: item.color }}
          />
          {item.name}
        </span>
      ))}
    </div>
  );
}

/** 차트 펜스 하나를 SVG로 그린다. 못 그리면 원본 표(또는 안내)를 대신 보여준다.
 *
 * hideLegend: 옆에 항목별 수치 목록을 따로 두는 화면용(서술 구성 통계). 같은 색·같은
 * 이름을 두 벌 그리면 읽는 사람이 둘을 대조하느라 오히려 느려진다. 기본은 종전대로 표시. */
export function ChartBlock({ source, hideLegend }: { source: string; hideLegend?: boolean }) {
  const [hover, setHover] = useState<HoverPoint | null>(null);
  const parsed = useMemo(() => {
    try {
      return { spec: parseChartSpec(source), error: null as string | null };
    } catch (e) {
      return { spec: null, error: e instanceof ChartSpecError ? e.message : "차트를 그릴 수 없음" };
    }
  }, [source]);

  const spec = parsed.spec;
  // 훅은 조건 앞에서 호출해야 한다 - 못 그리는 경우에도 순서가 흔들리지 않게.
  const scale = useScale(spec ?? ({ series: [{ name: "", values: [0] }] } as ChartSpec));

  if (spec === null) {
    return (
      <div className="my-4 rounded border border-fg-warning/40 bg-bg-warning px-3 py-2 text-xs text-fg-secondary">
        그래프를 그릴 수 없어 원본 표로 표시합니다 - {parsed.error}
      </div>
    );
  }

  return (
    <figure className="my-4">
      {spec.title && (
        // 캡션은 그림 위 - 실납품 보고서 실측 관례(HWPX 렌더와 동일).
        <figcaption className="mb-1 text-center text-sm font-semibold text-fg-secondary">
          {spec.title}
        </figcaption>
      )}
      <div className="relative">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full"
          role="img"
          aria-label={spec.title || "차트"}
        >
          {spec.unit && spec.type !== "pie" && (
            // 단위는 세로 축 라벨로 두지 않는다 - 90도 돌아간 한글은 읽기 나쁘다.
            <text x={PAD.left - 44} y={16} fontSize={11} fill={INK}>
              (단위: {spec.unit})
            </text>
          )}
          {spec.type !== "pie" && (
            <>
              <Axes scale={scale} />
              <XLabels spec={spec} />
            </>
          )}
          {spec.type === "bar" && <BarMarks spec={spec} scale={scale} onHover={setHover} />}
          {spec.type === "line" && <LineMarks spec={spec} scale={scale} onHover={setHover} />}
          {spec.type === "pie" && <PieMarks spec={spec} onHover={setHover} />}
        </svg>
        {hover && (
          <div
            className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded border border-border bg-bg px-2 py-1 text-xs shadow"
            style={{ left: `${(hover.x / W) * 100}%`, top: `${(hover.y / H) * 100}%` }}
          >
            <span className="font-medium text-fg">{hover.label}</span>
            <span className="ml-2 text-fg-secondary">
              {hover.series} {formatValue(hover.value)}
              {spec.unit ? ` ${spec.unit}` : ""}
            </span>
          </div>
        )}
      </div>
      {!hideLegend && (spec.series.length > 1 || spec.type === "pie") && <Legend spec={spec} />}
    </figure>
  );
}
