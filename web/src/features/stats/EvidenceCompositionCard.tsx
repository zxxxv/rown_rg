// 서술 구성 통계 카드 - 본문 문장이 어디까지 근거로 받쳐지는지 본다.
// 판정 칸은 절 근거 패널의 claimTone과 같은 기준이라 패널 숫자와 일치한다.
//
// 2026-08-29 개편: 전체+장을 파이 6개로 한 화면에 늘어놓던 것을 걷어냈다. 조각이
// 손톱만 해서 어느 것도 제대로 안 읽혔고(사용자 지적), 무엇보다 **퍼센트만 있고
// 실제 문장 수가 없어** "87%"가 몇 문장인지 알 수 없었다. 자료 사용 통계와 같은
// 방식으로 칩을 눌러 한 번에 하나씩 크게 보고, 옆에 개수와 비율을 같이 적는다.

import { useState } from "react";
import { type EvidenceTally, useEvidenceComposition } from "@/api/stats";
import { ChartBlock } from "@/features/preview/ChartBlock";
import { SERIES_COLORS } from "@/features/preview/chartSpec";
import { cn } from "@/lib/utils";

// 팔레트 5색 상한(조각당 한 색 규약) - 칸이 정확히 5개라 접기 없이 다 싣는다.
const SLICES: { key: keyof EvidenceTally; label: string; hint: string }[] = [
  { key: "confirmed", label: "근거 확인", hint: "원문 대목까지 특정한 문장" },
  { key: "unconfirmed", label: "확인 필요", hint: "인용은 했으나 대목 미특정(외국어 근거 포함)" },
  { key: "uncited", label: "AI 서술(표기 없음)", hint: "인용 표기가 없는 문장" },
  { key: "defect", label: "무근거 경고", hint: "검사에 걸린 문장" },
  {
    key: "uncovered",
    label: "대조 안 함",
    hint: "수치 없는 명사 종결 항목 등 대조 대상이 아닌 줄",
  },
];

/** 그림에 실제로 실리는 조각만, 그림과 **같은 순서로**.
 *
 * 색은 이 순서의 인덱스로 정해진다(ChartBlock: SERIES_COLORS[i]). 목록을 따로
 * 만들면 0인 칸이 빠진 만큼 색이 밀려 범례와 목록의 색이 어긋난다. */
function drawnSlices(tally: EvidenceTally) {
  return SLICES.map((s) => ({ ...s, value: tally[s.key] as number }))
    .filter((s) => s.value > 0)
    .map((s, i) => ({ ...s, color: SERIES_COLORS[i % SERIES_COLORS.length] }));
}

function pieFence(tally: EvidenceTally, title: string): string | null {
  const entries = drawnSlices(tally);
  if (entries.length < 2) return null; // 조각 1개는 그림이 아니다
  return [
    "type: pie",
    `title: ${title.replace(/\|/g, "·")}`,
    `x: ${entries.map((e) => e.label).join(" | ")}`,
    `series: 문장 = ${entries.map((e) => e.value).join(" | ")}`,
  ].join("\n");
}

function pct(value: number, denom: number): string {
  if (denom <= 0) return "0%";
  return `${Math.round((value / denom) * 100)}%`;
}

export function EvidenceCompositionCard({ projectId }: { projectId: string }) {
  const query = useEvidenceComposition(projectId, true);
  // 한 번에 하나만 크게 본다. ch=null이면 전체, 숫자면 그 장, sectionId까지 있으면 그 절.
  const [ch, setCh] = useState<number | null>(null);
  const [sectionId, setSectionId] = useState<string | null>(null);
  // 판정 기준 설명 - 다섯 칸 정의를 한 줄로 늘어놓으면 읽히지 않아(사용자 지적)
  // 물음표 뒤에 접어 둔다. 열면 칸별 한 줄씩.
  const [showHelp, setShowHelp] = useState(false);

  if (query.isLoading) {
    return (
      <p className="py-6 text-center text-sm text-fg-secondary">
        문장 판정을 집계하고 있습니다 - 첫 조회는 절마다 근거를 대조해 수십 초 걸릴 수 있습니다.
      </p>
    );
  }
  if (query.isError || !query.data) {
    return (
      <p className="py-6 text-center text-sm text-fg-secondary">
        서술 구성 통계를 불러오지 못했습니다.
      </p>
    );
  }

  const { total, chapters, sections } = query.data;
  const chapter = ch !== null ? chapters.find((c) => c.chapter_number === ch) : null;
  const section = sectionId !== null ? sections.find((s) => s.section_id === sectionId) : null;
  const chapterSections = ch !== null ? sections.filter((s) => s.chapter_number === ch) : [];

  const tally: EvidenceTally = section ?? chapter ?? total;
  const heading = section
    ? `${section.chapter_number}.${section.section_number} ${section.title}`
    : chapter
      ? `${chapter.chapter_number}장 ${chapter.title}`
      : "전체 보고서";

  const rows = drawnSlices(tally);
  // 그림에 실린 조각의 합 - 목록의 비율이 그림과 같은 분모를 쓰게 한다.
  // (claims는 '대조 대상'만이라 '대조 안 함'을 포함하지 않는다.)
  const drawn = rows.reduce((sum, r) => sum + r.value, 0);
  const fence = pieFence(tally, `${heading} 서술 구성`);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-1.5">
        <p className="text-xs text-fg-secondary">
          절 근거 패널과 같은 기준으로 문장을 가른 결과입니다.
        </p>
        <button
          type="button"
          onClick={() => setShowHelp((v) => !v)}
          aria-expanded={showHelp}
          aria-label="판정 기준 설명"
          className={cn(
            "flex h-4 w-4 shrink-0 items-center justify-center rounded-full border text-[10px] leading-none transition-colors",
            showHelp
              ? "border-accent bg-bg-info text-fg-info"
              : "border-border text-fg-secondary hover:border-border-strong hover:text-fg",
          )}
        >
          ?
        </button>
      </div>
      {showHelp ? (
        <ul className="flex flex-col gap-1 rounded border border-border bg-bg-secondary px-3 py-2 text-[11px] leading-relaxed text-fg-secondary">
          {SLICES.map((s) => (
            <li key={String(s.key)}>
              <b className="font-medium text-fg">{s.label}</b> - {s.hint}
            </li>
          ))}
        </ul>
      ) : null}

      {/* 범위 선택 - 장 드롭다운 + (장을 고르면) 절 드롭다운. 자료 사용 통계와 같은
          "하나 골라 하나만 크게" 방식이되, 장이 많아도 줄바꿈 없이 접히게 칩 대신
          드롭다운을 쓴다(2026-09-04 사용자 지시). */}
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={ch ?? ""}
          onChange={(e) => {
            setCh(e.target.value ? Number(e.target.value) : null);
            setSectionId(null);
          }}
          className="h-7 max-w-64 rounded border border-border bg-bg px-2 text-xs text-fg"
          aria-label="장 선택"
        >
          <option value="">전체 보고서</option>
          {chapters.map((c) => (
            <option key={c.chapter_number} value={c.chapter_number}>
              {c.chapter_number}장 {c.title}
            </option>
          ))}
        </select>
        {ch !== null && chapterSections.length > 0 ? (
          <select
            value={sectionId ?? ""}
            onChange={(e) => setSectionId(e.target.value || null)}
            className="h-7 max-w-64 rounded border border-border bg-bg px-2 text-xs text-fg"
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
      {ch !== null && sections.length === 0 ? (
        // v2 저장분은 절 배열이 없다 - 본문이 바뀌어 재계산될 때 채워진다.
        <p className="text-[11px] text-fg-tertiary">절 단위 집계는 다음 재계산부터 표시됩니다.</p>
      ) : null}

      {/* 요약 줄 - 퍼센트만 있으면 "87%가 몇 문장인지"를 알 수 없다. 개수를 먼저 적는다. */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-fg-secondary">
        <span className="font-medium text-fg">{heading}</span>
        <span>
          대조 <b className="font-mono text-fg">{tally.claims.toLocaleString()}</b>문장
        </span>
        <span>
          근거 확인 <b className="font-mono text-fg">{tally.confirmed.toLocaleString()}</b>문장 (
          {pct(tally.confirmed, tally.claims)})
        </span>
        {tally.uncovered > 0 ? (
          <span>
            대조 안 함 <b className="font-mono text-fg">{tally.uncovered.toLocaleString()}</b>줄
          </span>
        ) : null}
        {tally.crosslingual > 0 ? (
          <span className="text-fg-tertiary">
            외국어 근거 <b className="font-mono">{tally.crosslingual.toLocaleString()}</b>문장
          </span>
        ) : null}
      </div>

      {drawn === 0 ? (
        <p className="text-xs text-fg-tertiary">이 범위에는 판정할 문장이 없습니다.</p>
      ) : (
        <div
          className={cn(
            "grid grid-cols-1 gap-5",
            // 조각이 1개뿐이면 그림이 없다 - 두 칸으로 두면 왼쪽 520px이 빈 채로 남는다.
            fence && "lg:grid-cols-[minmax(0,520px)_minmax(0,1fr)]",
          )}
        >
          {fence ? (
            <div className="min-w-0">
              <ChartBlock source={fence} hideLegend />
            </div>
          ) : null}
          {/* 폭을 묶어 둔다 - flex-1인 라벨이 남는 폭을 다 먹으면 이름과 숫자가 화면
              양끝으로 벌어져 한 줄을 읽는 데 시선이 멀리 이동한다. */}
          <div className="flex min-w-0 max-w-lg flex-col justify-center gap-2">
            {rows.map((row) => (
              <div key={String(row.key)} className="flex items-center gap-2 text-xs">
                <span
                  aria-hidden
                  className="h-2.5 w-2.5 shrink-0 rounded-sm"
                  style={{ backgroundColor: row.color }}
                />
                <span className="min-w-0 flex-1 truncate text-fg" title={row.hint}>
                  {row.label}
                </span>
                <span className="w-24 shrink-0">
                  <span className="block h-1.5 overflow-hidden rounded bg-bg-secondary">
                    <span
                      className="block h-full rounded"
                      style={{
                        width: `${Math.max((row.value / drawn) * 100, 2)}%`,
                        backgroundColor: row.color,
                      }}
                    />
                  </span>
                </span>
                <span className="w-24 shrink-0 text-right font-mono text-fg-secondary">
                  {row.value.toLocaleString()}문장 {pct(row.value, drawn)}
                </span>
              </div>
            ))}
            <p className="mt-1 text-[11px] leading-relaxed text-fg-tertiary">
              비율은 위 {drawn.toLocaleString()}문장 기준입니다. '대조 안 함'은 수치 없는 명사 종결
              항목처럼 애초에 근거를 댈 대상이 아닌 줄이라 '대조 {tally.claims.toLocaleString()}
              문장'에는 들어가지 않습니다.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
