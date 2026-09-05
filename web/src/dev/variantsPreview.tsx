// 안 고르기 육안 확인용 하네스 - 앱 로그인·API 없이 패널만 실제 CSS로 그린다.
// react-query 캐시에 variants 응답을 직접 심어 네트워크 없이 렌더한다.
//
// 이 패널에서 눈으로만 잡히는 것들:
//   - 미리보기가 날 마크다운(#, 표 파이프)이 아니라 지면 모양으로 그려지는가
//   - 접힌 상태(max-h)에서 표·헤딩이 카드 밖으로 삐져나오지 않는가
//   - 전문 보기로 펼쳤을 때 표가 제대로 서고 스크롤이 갇히는가
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import type { SectionVariants } from "@/api/variants";
import { SectionVariantsPanel } from "@/features/preview/SectionVariants";
import "@/styles/global.css";

const CURRENT = `# 2.1 국내외 산업·시장 동향 분석

세계 철강산업은 탄소가격 부과 여부를 기준으로 생산체계가 분화되는 국면에 진입하였고, 이 분화는 통상규제와 결합하여 수출 경쟁력을 좌우하는 변수로 작동하고 있음. 이 절은 그 가운데 주요국의 탈탄소 전환 투자를 견준다(출처 3).

ㅇ 글로벌 조강 생산은 18.9억 톤 수준에서 정체되어 있으며, 수요 성장은 인도·동남아에 집중됨(출처 5)`;

const VARIANT_TABLE = `# 2.1 국내외 산업·시장 동향 분석

표: 산업·시장 동향이 가리키는 지원 소요 근거와 성과 영역
(단위: 건)
| 지원 축 | 동향상 압력 요인 | 확인 가능한 성과 영역 |
|---|---|---|
| 수소환원제철 | EU CBAM 본격 부과 | 조강 톤당 배출 원단위 |
| 전기로 전환 | 저탄소 철강 수요 확대 | 전환 투자 집행률 |
(출처 7)

ㅇ 위 표의 압력 요인은 3장의 지원 타당성 판단과 연결됨(출처 7)`;

const VARIANT_PROSE = `# 2.1 국내외 산업·시장 동향 분석

## 2.1.1 글로벌 산업·시장 환경

세계 철강 시장은 2024년 기준 조강 생산 18.9억 톤으로 정체 국면이며, 탄소규제 강화에 따라 저탄소 철강 프리미엄 시장이 형성되고 있음(출처 2). 유럽 주요 수요처는 2030년까지 저탄소 철강 조달 비중을 확대하겠다고 공표함(출처 9).`;

const DATA: SectionVariants = {
  running: false,
  total: 2,
  done: 2,
  failures: {},
  variants: [
    {
      id: "v1",
      content: VARIANT_TABLE,
      n_chars: VARIANT_TABLE.length,
      n_markers: 21,
      evidence_count: 24,
      volume_scaled: false,
    },
    {
      id: "v2",
      content: VARIANT_PROSE,
      n_chars: VARIANT_PROSE.length,
      n_markers: 23,
      evidence_count: 24,
      volume_scaled: true,
    },
  ],
};

function App() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(["sections", "content", "p1", "s1", "variants"], DATA);
  return (
    <QueryClientProvider client={client}>
      <div className="min-h-screen bg-bg p-6">
        <h1 className="mb-4 text-sm font-semibold text-fg">안 고르기 확인</h1>
        <div className="max-w-3xl">
          <SectionVariantsPanel projectId="p1" sectionId="s1" currentContent={CURRENT} />
        </div>
      </div>
    </QueryClientProvider>
  );
}

createRoot(document.getElementById("root") as HTMLElement).render(<App />);
