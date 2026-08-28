// 차트 렌더 육안 확인용 하네스 - 앱 로그인·API 없이 ChartBlock만 실제 CSS로 그린다.
// 컴파일된다 != 제대로 그려진다. 한글 PNG에서 축 라벨 회전·라벨 잘림·y축 바닥을
// 눈으로 보고서야 잡았던 전례가 있어(2026-08-11), 웹 SVG도 같은 눈으로 확인한다.
import { useState } from "react";
import { createRoot } from "react-dom/client";
import { ChartConvertDialog } from "@/features/preview/ChartConvertDialog";
import { chartFallbackTable, toFence } from "@/features/preview/chartSpec";
import { MarkdownContent } from "@/features/preview/MarkdownContent";
import { buildSpec, defaultChoice, findTable } from "@/features/preview/tableToChart";
import "@/styles/global.css";

const CASES: Array<{ label: string; md: string }> = [
  {
    label: "막대 · 계열 1개 (값 라벨)",
    md: [
      "```chart",
      "type: bar",
      "title: 주요국 SMR 누적 투자액",
      "unit: 억 달러",
      "x: 미국 | 중국 | 프랑스 | 대한민국 | 일본",
      "series: 투자액 = 1,240 | 950 | 430 | 300 | 210",
      "source: 3, 7",
      "```",
    ].join("\n"),
  },
  {
    label: "막대 · 계열 3개 (값 라벨 생략·범례)",
    md: [
      "```chart",
      "type: bar",
      "title: 연도별 정부 R&D 예산 배분",
      "unit: 조 원",
      "x: 2021년 | 2022년 | 2023년 | 2024년",
      "series: 기초연구 = 7.2 | 7.8 | 8.1 | 8.9",
      "series: 응용개발 = 5.1 | 5.4 | 5.9 | 6.3",
      "series: 인프라 = 2.4 | 2.6 | 2.9 | 3.4",
      "```",
    ].join("\n"),
  },
  {
    label: "꺾은선 · 증가 추세 (0 바닥·끝점 라벨)",
    md: [
      "```chart",
      "type: line",
      "title: 반도체 수출액 추이",
      "unit: 억 달러",
      "x: 2020년 | 2021년 | 2022년 | 2023년 | 2024년",
      "series: 수출액 = 992 | 1,280 | 1,292 | 986 | 1,419",
      "series: 수입액 = 570 | 690 | 745 | 680 | 812",
      "```",
    ].join("\n"),
  },
  {
    label: "원형 · 구성비",
    md: [
      "```chart",
      "type: pie",
      "title: 재생에너지 발전 비중",
      "x: 태양광 | 풍력 | 수력 | 바이오 | 기타",
      "series: 비중 = 46.2 | 21.5 | 15.3 | 11.7 | 5.3",
      "```",
    ].join("\n"),
  },
  {
    label: "긴 한글 x축 라벨 (겹침 확인)",
    md: [
      "```chart",
      "type: bar",
      "title: 부문별 온실가스 감축 목표",
      "unit: 백만 톤",
      "x: 전환부문 | 산업부문 | 건물부문 | 수송부문 | 농축수산부문",
      "series: 감축량 = 145.9 | 222.6 | 35.5 | 61.0 | 18.0",
      "```",
    ].join("\n"),
  },
  {
    label: "막대 · 음수 섞임 (0선 아래로 자라야 함)",
    md: [
      "```chart",
      "type: bar",
      "title: CCA 1년차 대상 업종별 수입액 변화 추정",
      "unit: million US$",
      "x: 석유정제 | 펄프·제지 | 비금속광물 | 철강 | 비철금속",
      "series: 수입액 변화 = 160 | -36 | -674 | -934 | -966",
      "source: 11",
      "```",
    ].join("\n"),
  },
  {
    label: "막대 · 전부 음수에 아주 작은 값 (라벨이 '0'이 되면 안 됨)",
    md: [
      "```chart",
      "type: bar",
      "title: 미국 CCA 시행에 따른 품목별 산출 변화율",
      "unit: %",
      "x: 시멘트 | 알루미늄 | 철·철강 | 펄프·제지",
      "series: 산출 변화율 = -0.02 | -1.9 | -0.6 | -0.3",
      "```",
    ].join("\n"),
  },
  {
    label: "꺾은선 · 음수 구간 (0선 교차)",
    md: [
      "```chart",
      "type: line",
      "title: 분기별 교역수지",
      "unit: 억 달러",
      "x: 2023년 1분기 | 2023년 2분기 | 2023년 3분기 | 2023년 4분기",
      "series: 수지 = 42 | -18 | -55 | 27",
      "```",
    ].join("\n"),
  },
  {
    label: "폴백 · 값 개수 불일치 (원본 표로 되돌아가야 함)",
    md: [
      "```chart",
      "type: bar",
      "title: 값이 모자란 차트",
      "x: 가 | 나 | 다",
      "series: 값 = 1 | 2",
      "table: |",
      "  | 구분 | 값 |",
      "  |------|-----|",
      "  | 가 | 1 |",
      "  | 나 | 2 |",
      "```",
    ].join("\n"),
  },
  {
    label: "본문 속 차트 (문단·표와 함께)",
    md: [
      "□ 주요국은 소형모듈원자로(SMR) 상용화를 국가 과제로 삼고 투자를 늘리고 있다 (출처 3).",
      "",
      "```chart",
      "type: line",
      "title: SMR 관련 특허 출원 건수",
      "unit: 건",
      "x: 2019년 | 2020년 | 2021년 | 2022년 | 2023년",
      "series: 출원 건수 = 310 | 402 | 528 | 690 | 915",
      "source: 3",
      "```",
      "",
      "ㅇ 같은 기간 국내 출원은 연평균 31% 늘었다.",
    ].join("\n"),
  },
];

// 변환 대화상자 확인용 표본 블록 - 실제 본문에 나오는 모양(제목 줄 + 표 + 출처 표기).
const TABLE_BLOCK = [
  "표: 주요국 SMR 누적 투자액 (출처 3)",
  "",
  "| 국가 | 투자액(억 달러) | 기업 수 |",
  "|------|------|------|",
  "| 미국 | 1,240 | 18 |",
  "| 중국 | 950 | 12 |",
  "| 프랑스 | 430 | 6 |",
  "| 대한민국 | 300 | 5 |",
  "| 일본 | 210 | 4 |",
].join("\n");

// 실제 보고서에서 가져온 표 - 구간("643~750GWh")과 변화("43.8GW → 508GW")가 섞여 있어
// 첫 숫자만 집으면 조용히 틀린 그래프가 된다. 경고가 뜨는지 보는 표본.
const AMBIGUOUS_BLOCK = [
  "| 구분 | 수치 | 의미 |",
  "|------|------|------|",
  "| 2023년 글로벌 배터리 수요 | 643~750GWh | 전년 대비 37~40% 증가한 시장 수요 규모임 |",
  "| 2026~2031년 이차전지 시장 | 4,377억 9천만 달러 | 연평균 18.31% 성장 전망임 |",
  "| 2022~2030년 ESS 설비 규모 | 43.8GW/91.5GWh → 508GW/1,432GWh | 연평균 23% 증가 전망임 |",
  "| 2026~2031년 고체 배터리 성장률 | 연평균 24.9% | 차세대 분야 중 가장 높은 성장률로 제시됨 |",
  "| 중국 첨단 배터리 정부투자 | 18.4억 위안 | 27개 프로젝트에 배분된 정부 투자 규모임 |",
].join("\n");

// 실제 보고서의 정책 표 - 규모 열에 "—"와 "명"이 섞여 있어 그래프로 만들 값이 아니다.
// 아래 WON_ONLY는 "—"만 없앤 판으로, 한글 수 단위가 값을 잘라 먹는지 보는 표본이다
// ("1조 96억 원" -> 1이 되면 4,027억(4027)보다 작게 그려진다).
const POLICY_BLOCK = [
  "표: 정책 구분별 주요 사업명·규모",
  "",
  "| 정책 구분 | 주요 사업명 | 규모 | 추진 기간 |",
  "|------|------|------|------|",
  "| AI 반도체 원천기술 | 차세대지능형반도체기술개발사업 | 1조 96억 원 | 2020~2029년 |",
  "| PIM 반도체 | PIM 인공지능반도체기술개발사업 | 4,027억 원 | 2022~2028년 |",
  "| 팹리스 육성 | 글로벌 K-팹리스 육성 수요 연계형 R&D | — | 2021년~ |",
  "| 인력양성 | AI 반도체 대학원 등 | 3만 6,000명(10년) | 중장기 |",
].join("\n");

const WON_ONLY_BLOCK = [
  "표: 사업별 예산",
  "",
  "| 사업 | 예산 |",
  "|------|------|",
  "| 차세대지능형반도체 | 1조 96억 원 |",
  "| PIM 인공지능반도체 | 4,027억 원 |",
  "| K-팹리스 | 2,500억 원 |",
].join("\n");

function DialogCase({ block }: { block: string }) {
  const table = findTable(block);
  const [fence, setFence] = useState<string | null>(null);
  if (table === null) return <p>표를 못 읽었습니다</p>;
  return (
    <div className="p-4">
      <ChartConvertDialog
        open
        onOpenChange={() => undefined}
        table={table}
        block={block}
        busy={false}
        onConvert={setFence}
      />
      {fence ? <pre className="mt-4 whitespace-pre-wrap text-xs">{fence}</pre> : null}
    </div>
  );
}

/** 왕복 확인 - 표 -> 펜스 -> 본문 렌더 -> 표로 되돌리기가 원본과 같은지 눈으로 본다. */
function RoundTripCase() {
  const table = findTable(TABLE_BLOCK);
  if (table === null) return <p>표를 못 읽었습니다</p>;
  const fence = toFence(buildSpec(table, defaultChoice(table), TABLE_BLOCK));
  const reverted = chartFallbackTable(fence);
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 bg-bg p-8 text-sm">
      <section className="rounded border border-border p-4">
        <h2 className="mb-2 text-sm font-semibold text-fg-secondary">1. 원본 블록</h2>
        <MarkdownContent content={TABLE_BLOCK} />
      </section>
      <section className="rounded border border-border p-4">
        <h2 className="mb-2 text-sm font-semibold text-fg-secondary">2. 변환 후 본문 렌더</h2>
        <MarkdownContent content={fence} />
      </section>
      <section className="rounded border border-border p-4">
        <h2 className="mb-2 text-sm font-semibold text-fg-secondary">
          3. 표로 되돌리기 {reverted.trim() === TABLE_BLOCK.trim() ? "(원본과 일치)" : "(불일치!)"}
        </h2>
        <MarkdownContent content={reverted} />
      </section>
    </div>
  );
}

// ?case=N 이면 그 하나만 - 한 장에 다 담으면 축소돼 라벨 겹침을 못 본다.
const only = new URLSearchParams(window.location.search).get("case");
const shown = only === null ? CASES : [CASES[Number(only)] ?? CASES[0]];

function Harness() {
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-8 bg-bg p-8">
      {shown.map((c) => (
        <section key={c.label} className="rounded border border-border p-4">
          <h2 className="mb-2 text-sm font-semibold text-fg-secondary">{c.label}</h2>
          <MarkdownContent content={c.md} />
        </section>
      ))}
    </div>
  );
}

const el = document.getElementById("root");
if (el) {
  const view =
    only === "dialog" ? (
      <DialogCase block={TABLE_BLOCK} />
    ) : only === "ambiguous" ? (
      <DialogCase block={AMBIGUOUS_BLOCK} />
    ) : only === "policy" ? (
      <DialogCase block={POLICY_BLOCK} />
    ) : only === "won" ? (
      <DialogCase block={WON_ONLY_BLOCK} />
    ) : only === "roundtrip" ? (
      <RoundTripCase />
    ) : (
      <Harness />
    );
  createRoot(el).render(view);
}
