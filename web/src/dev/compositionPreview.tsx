// 서술 구성 통계 육안 확인용 하네스 - 앱 로그인·API 없이 카드만 실제 CSS로 그린다.
// react-query 캐시에 응답을 직접 심어 네트워크 없이 렌더한다.
//
// 컴파일된다 != 제대로 그려진다. 이 카드에서 눈으로만 잡히는 것들:
//   - 파이 조각 색과 오른쪽 목록의 색점이 어긋나는가(0인 칸이 빠지면 색이 밀린다)
//   - "1,478문장 87%"가 숫자 칸을 넘치는가
//   - 조각이 1개뿐이라 그림이 없을 때 왼쪽 520px이 빈 채로 남는가
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import type { EvidenceCompositionResponse } from "@/api/stats";
import { EvidenceCompositionCard } from "@/features/stats/EvidenceCompositionCard";
import "@/styles/global.css";

type Tally = {
  claims: number;
  confirmed: number;
  crosslingual: number;
  unconfirmed: number;
  uncited: number;
  defect: number;
  uncovered: number;
};

function tally(
  confirmed: number,
  unconfirmed: number,
  uncited: number,
  defect: number,
  uncovered: number,
  crosslingual = 0,
): Tally {
  return {
    claims: confirmed + unconfirmed + uncited + defect,
    confirmed,
    crosslingual,
    unconfirmed,
    uncited,
    defect,
    uncovered,
  };
}

// 실제 운영 화면의 자릿수를 그대로 - 네 자리 수가 칸을 넘치는지 봐야 한다.
const NORMAL: EvidenceCompositionResponse = {
  total: tally(102, 1478, 78, 39, 63, 816),
  chapters: [
    {
      chapter_number: 1,
      title: "글로벌 탄소규제 및 국제표준 최신 동향 정밀조사",
      ...tally(14, 355, 72, 6, 5, 215),
    },
    {
      chapter_number: 2,
      title: "주요 산업군별 글로벌 탄소규제 요구사항 분석 및 규제영향 매핑",
      ...tally(0, 190, 69, 25, 1, 202),
    },
  ],
  sections: [
    {
      section_id: "s1",
      chapter_number: 1,
      section_number: 1,
      title: "국제 감축목표와 규제 지형",
      ...tally(9, 180, 30, 2, 3, 100),
    },
    {
      section_id: "s2",
      chapter_number: 1,
      section_number: 2,
      title: "주요국 제도 비교",
      ...tally(5, 175, 42, 4, 2, 115),
    },
    {
      section_id: "s3",
      chapter_number: 2,
      section_number: 1,
      title: "철강·시멘트 요구사항",
      ...tally(0, 190, 69, 25, 1, 202),
    },
  ],
};

// 조각이 1개뿐 - 그림이 없다. 왼쪽 칸이 비어 남지 않아야 한다.
const SINGLE: EvidenceCompositionResponse = {
  total: tally(0, 0, 44, 0, 0),
  chapters: [{ chapter_number: 1, title: "표기 없는 장", ...tally(0, 0, 44, 0, 0) }],
  sections: [],
};

// 0인 칸이 섞였다 - 파이는 그 조각을 빼고 그리므로 목록 색점도 같이 밀려야 한다.
const WITH_ZERO: EvidenceCompositionResponse = {
  total: tally(120, 300, 0, 45, 0, 60),
  chapters: [{ chapter_number: 1, title: "빈 칸이 있는 장", ...tally(120, 300, 0, 45, 0, 60) }],
  sections: [],
};

const CASES: { id: string; label: string; data: EvidenceCompositionResponse }[] = [
  {
    id: "p-normal",
    label: "정상 - 5칸 + 장/절 드롭다운(1장을 골라야 절이 나온다) + 물음표 도움말",
    data: NORMAL,
  },
  { id: "p-zero", label: "0인 칸 포함 - 파이 조각과 목록 색점이 같아야 한다", data: WITH_ZERO },
  { id: "p-single", label: "조각 1개 - 그림 없음. 목록이 폭을 다 써야 한다", data: SINGLE },
];

function App() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  for (const c of CASES) client.setQueryData(["evidence-composition", c.id], c.data);
  return (
    <QueryClientProvider client={client}>
      <div className="min-h-screen bg-bg-secondary p-6">
        <h1 className="mb-4 text-sm font-semibold text-fg">서술 구성 통계 확인</h1>
        <div className="flex flex-col gap-6">
          {CASES.map((c) => (
            <section key={c.id} className="rounded-lg border border-border bg-bg p-4">
              <p className="mb-3 text-xs font-medium text-fg-secondary">{c.label}</p>
              <EvidenceCompositionCard projectId={c.id} />
            </section>
          ))}
        </div>
      </div>
    </QueryClientProvider>
  );
}

createRoot(document.getElementById("root") as HTMLElement).render(<App />);
