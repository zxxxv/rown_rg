// 자료 사용 통계 카드 - 완성 보고서가 어떤 자료 위에 서 있는지 전체/장/절로 본다.
// 근거 드로어(블록→원문, 미시)와 층이 다르다: 이건 보고서 단위의 거시 조감이고,
// 인용 집중(한 자료 편중)과 미사용 채택 자료를 드러내 다음 런의 자료 구성을 돕는다.

import { AlertTriangle } from "lucide-react";
import { useMemo, useState } from "react";
import { type SourceUsageItem, useSourceUsage } from "@/api/stats";
import { ChartBlock } from "@/features/preview/ChartBlock";
import { cn } from "@/lib/utils";

const ORIGIN_LABEL: Record<string, string> = {
  upload: "업로드",
  library: "라이브러리",
  web_search: "웹",
};

// 인용 집중 경고 임계 - 서버 실측(고급 런 1위 자료가 전체 인용의 30%, 육안으로도
// 단일 출처 반복이 확인됨)에서 잡은 보수적 선. 표본이 쌓이면 조정한다.
const CONCENTRATION_WARN_SHARE = 0.4;
const CONCENTRATION_MIN_CITATIONS = 20;

/** 도넛용 차트 펜스 본문 - 상위 4개 + 기타(팔레트 5색 상한, 조각당 한 색 규약). */
function pieFence(
  counts: Record<string, number>,
  byNumber: Map<number, SourceUsageItem>,
  title: string,
): string | null {
  const entries = Object.entries(counts)
    .map(([k, v]) => ({ n: Number(k), v }))
    .sort((a, b) => b.v - a.v);
  if (entries.length < 2) return null; // 조각 1개는 그림이 아니다 - 목록만 보여준다
  const top = entries.slice(0, 4);
  const rest = entries.slice(4);
  const label = (n: number) => {
    // '|'는 스펙 구분자라 제목에서 치환한다. 범례가 카드 폭을 넘지 않게 자른다.
    const t = (byNumber.get(n)?.title ?? `출처 ${n}`).replace(/\|/g, "·");
    return `${n}. ${t.length > 14 ? `${t.slice(0, 13)}…` : t}`;
  };
  const x = top.map((e) => label(e.n));
  const values = top.map((e) => e.v);
  if (rest.length > 0) {
    x.push(`기타 ${rest.length}건`);
    values.push(rest.reduce((s, e) => s + e.v, 0));
  }
  return [
    "type: pie",
    `title: ${title.replace(/\|/g, "·")}`,
    `x: ${x.join(" | ")}`,
    `series: 참조 = ${values.join(" | ")}`,
  ].join("\n");
}

export function SourceUsageCard({ projectId }: { projectId: string }) {
  const query = useSourceUsage(projectId, true);
  // 드릴다운 상태: ch=null이면 전체, sectionId까지 있으면 절 단위.
  const [ch, setCh] = useState<number | null>(null);
  const [sectionId, setSectionId] = useState<string | null>(null);

  const data = query.data;
  const byNumber = useMemo(() => {
    const m = new Map<number, SourceUsageItem>();
    for (const x of [...(data?.sources ?? []), ...(data?.unused ?? [])]) m.set(x.number, x);
    return m;
  }, [data]);

  if (query.isLoading) {
    return <p className="text-xs text-fg-tertiary">통계를 불러오는 중…</p>;
  }
  if (!data) {
    return <p className="text-xs text-fg-tertiary">통계를 불러오지 못했습니다.</p>;
  }

  const chapter = ch !== null ? data.chapters.find((c) => c.chapter_number === ch) : null;
  const section = sectionId !== null ? data.sections.find((s) => s.section_id === sectionId) : null;
  const chapterSections = ch !== null ? data.sections.filter((s) => s.chapter_number === ch) : [];

  const scope = section
    ? {
        heading: `${section.chapter_number}.${section.section_number} ${section.title}`,
        counts: section.counts,
        citations: section.citations,
      }
    : chapter
      ? {
          heading: `${chapter.chapter_number}장 ${chapter.title}`,
          counts: chapter.counts,
          citations: chapter.citations,
        }
      : {
          heading: "전체 보고서",
          counts: Object.fromEntries(data.sources.map((x) => [String(x.number), x.citations])),
          citations: data.total_citations,
        };

  const rows = Object.entries(scope.counts)
    .map(([k, v]) => ({ n: Number(k), v }))
    .sort((a, b) => b.v - a.v);
  const top = rows[0];
  const topShare = top && scope.citations > 0 ? top.v / scope.citations : 0;
  const fence = pieFence(scope.counts, byNumber, `${scope.heading} 자료 사용 비중`);

  // 원천 구성(전체 고정) - 인용이 어디서 왔는가(업로드/웹/라이브러리)
  const originTotals = data.sources.reduce<Record<string, number>>((acc, x) => {
    acc[x.origin] = (acc[x.origin] ?? 0) + x.citations;
    return acc;
  }, {});

  return (
    <div className="flex flex-col gap-3">
      {/* 요약 줄 - 보고서 전체의 한 줄 조감 */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-secondary">
        <span>
          참조 <b className="font-mono text-fg">{data.total_citations.toLocaleString()}</b>회
        </span>
        <span>
          사용 자료{" "}
          <b className="font-mono text-fg">
            {data.sources.length}/{data.sources.length + data.unused.length}
          </b>
          건
        </span>
        {Object.entries(originTotals).map(([origin, n]) => (
          <span key={origin}>
            {ORIGIN_LABEL[origin] ?? origin}{" "}
            <b className="font-mono text-fg">
              {data.total_citations > 0 ? Math.round((n / data.total_citations) * 100) : 0}%
            </b>
          </span>
        ))}
      </div>

      {/* 레벨 선택 - 전체/장 칩 + (장 선택 시) 절 드릴다운 */}
      <div className="flex flex-wrap items-center gap-1.5">
        <LevelChip
          active={ch === null}
          onClick={() => {
            setCh(null);
            setSectionId(null);
          }}
        >
          전체
        </LevelChip>
        {data.chapters.map((c) => (
          <LevelChip
            key={c.chapter_number}
            active={ch === c.chapter_number}
            onClick={() => {
              setCh(c.chapter_number);
              setSectionId(null);
            }}
          >
            {c.chapter_number}장
          </LevelChip>
        ))}
        {ch !== null ? (
          <select
            value={sectionId ?? ""}
            onChange={(e) => setSectionId(e.target.value || null)}
            className="h-7 rounded border border-border bg-bg px-2 text-xs text-fg"
            aria-label="절 선택"
          >
            <option value="">장 전체</option>
            {chapterSections.map((s) => (
              <option key={s.section_id} value={s.section_id}>
                {s.chapter_number}.{s.section_number} {s.title}
              </option>
            ))}
          </select>
        ) : null}
      </div>

      {scope.citations === 0 ? (
        <p className="text-xs text-fg-tertiary">이 범위에는 인용 표기가 없습니다.</p>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)]">
          {fence ? (
            <div className="min-w-0">
              <ChartBlock source={fence} />
            </div>
          ) : null}
          <div className="flex min-w-0 flex-col gap-1.5">
            {topShare >= CONCENTRATION_WARN_SHARE &&
            scope.citations >= CONCENTRATION_MIN_CITATIONS &&
            top ? (
              <p className="flex items-center gap-1.5 text-xs text-fg-warning">
                <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
                인용의 {Math.round(topShare * 100)}%가 [{top.n}] 한 자료에 몰려 있습니다 - 교차
                검증용 자료 보강을 권합니다.
              </p>
            ) : null}
            {rows.map(({ n, v }) => {
              const item = byNumber.get(n);
              const pct = scope.citations > 0 ? (v / scope.citations) * 100 : 0;
              return (
                <div key={n} className="flex items-center gap-2 text-xs">
                  <span className="w-7 shrink-0 text-right font-mono text-fg-tertiary">[{n}]</span>
                  <span className="min-w-0 flex-1 truncate text-fg" title={item?.title}>
                    {item?.title ?? "(제목 없음)"}
                  </span>
                  <span className="shrink-0 rounded bg-bg-secondary px-1 py-0.5 text-[10px] text-fg-tertiary">
                    {ORIGIN_LABEL[item?.origin ?? ""] ?? item?.origin}
                  </span>
                  <span className="w-24 shrink-0">
                    <span className="block h-1.5 overflow-hidden rounded bg-bg-secondary">
                      <span
                        className="block h-full rounded bg-accent"
                        style={{ width: `${Math.max(pct, 2)}%` }}
                      />
                    </span>
                  </span>
                  <span className="w-14 shrink-0 text-right font-mono text-fg-secondary">
                    {v}회 {Math.round(pct)}%
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {data.unused.length > 0 ? (
        <details className="text-xs text-fg-secondary">
          <summary className="cursor-pointer select-none">
            한 번도 인용되지 않은 채택 자료 {data.unused.length}건 - 다음 생성에서 빼거나 바꿀 후보
          </summary>
          <ul className="mt-1.5 flex flex-col gap-1 pl-1">
            {data.unused.map((x) => (
              <li key={x.number} className="flex items-center gap-2">
                <span className="w-7 shrink-0 text-right font-mono text-fg-tertiary">
                  [{x.number}]
                </span>
                <span className="min-w-0 flex-1 truncate" title={x.title}>
                  {x.title}
                </span>
                <span className="shrink-0 rounded bg-bg-secondary px-1 py-0.5 text-[10px] text-fg-tertiary">
                  {ORIGIN_LABEL[x.origin] ?? x.origin}
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

function LevelChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "h-7 rounded-full border px-2.5 text-xs transition-colors",
        active
          ? "border-accent bg-bg-info text-fg-info"
          : "border-border bg-bg text-fg-secondary hover:border-border-strong",
      )}
    >
      {children}
    </button>
  );
}
