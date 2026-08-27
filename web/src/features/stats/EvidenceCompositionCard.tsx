// 서술 구성 통계 카드 - 본문 문장이 어디까지 근거로 받쳐지는지 전체/장을 같은
// 파이로 나란히 본다(2026-08-28 요청: AI 서술과 인용 비율을 전체와 장 동일 형식으로).
// 판정 칸은 절 근거 패널의 claimTone과 같은 기준이라 패널 숫자와 일치한다.

import { type EvidenceTally, useEvidenceComposition } from "@/api/stats";
import { ChartBlock } from "@/features/preview/ChartBlock";

// 팔레트 5색 상한(조각당 한 색 규약) - 칸이 정확히 5개라 접기 없이 다 싣는다.
const SLICES: { key: keyof EvidenceTally; label: string }[] = [
  { key: "confirmed", label: "근거 확인" },
  { key: "unconfirmed", label: "확인 필요" },
  { key: "uncited", label: "AI 서술(표기 없음)" },
  { key: "defect", label: "무근거 경고" },
  { key: "uncovered", label: "대조 안 함" },
];

function pieFence(tally: EvidenceTally, title: string): string | null {
  const entries = SLICES.map((s) => ({ label: s.label, v: tally[s.key] })).filter((e) => e.v > 0);
  if (entries.length < 2) return null; // 조각 1개는 그림이 아니다
  return [
    "type: pie",
    `title: ${title.replace(/\|/g, "·")}`,
    `x: ${entries.map((e) => e.label).join(" | ")}`,
    `series: 문장 = ${entries.map((e) => e.v).join(" | ")}`,
  ].join("\n");
}

function confirmedShare(tally: EvidenceTally): string {
  const denom = tally.claims;
  if (denom === 0) return "-";
  return `${Math.round((tally.confirmed / denom) * 100)}%`;
}

export function EvidenceCompositionCard({ projectId }: { projectId: string }) {
  const query = useEvidenceComposition(projectId, true);

  if (query.isLoading) {
    return (
      <p className="py-6 text-center text-sm text-fg-secondary">
        문장 판정을 집계하고 있습니다 - 첫 조회는 절마다 근거를 대조해 수십 초 걸릴 수
        있습니다.
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
  const { total, chapters } = query.data;
  const cells: { title: string; tally: EvidenceTally }[] = [
    { title: "전체", tally: total },
    ...chapters.map((ch) => ({
      title: `${ch.chapter_number}장 ${ch.title}`,
      tally: ch as EvidenceTally,
    })),
  ];

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-fg-secondary">
        절 근거 패널과 같은 기준으로 문장을 가른 결과입니다. 근거 확인=원문 대목까지 특정,
        확인 필요=인용은 했으나 대목 미특정(외국어 근거 포함), AI 서술=인용 표기가 없는
        문장, 무근거 경고=검사에 걸린 문장, 대조 안 함=수치 없는 명사 종결 항목 등 대조
        대상이 아닌 줄.
      </p>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {cells.map(({ title, tally }) => {
          const fence = pieFence(tally, title);
          return (
            <div key={title} className="rounded-lg border border-border bg-bg p-3">
              {fence ? (
                <ChartBlock source={fence} />
              ) : (
                <p className="py-4 text-center text-xs text-fg-secondary">
                  {title}: 표시할 분포가 없습니다
                </p>
              )}
              <p className="mt-1 text-center text-xs text-fg-secondary">
                대조 {tally.claims}문장 · 근거 확인률 {confirmedShare(tally)}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
