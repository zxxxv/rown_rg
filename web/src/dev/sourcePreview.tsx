// 원문 뷰어 육안 확인용 하네스 - 앱 로그인·API 없이 SourceViewer만 실제 CSS로 그린다.
// react-query 캐시에 응답을 직접 심어 네트워크 없이 렌더한다.
// 파싱된 실제 원문의 지저분한 경우(표·소제목·굵은 글씨·각주 기호)를 한 화면에 모아,
// 렌더된 쪽과 원문 그대로 쪽에서 같은 대목이 강조되는지 눈으로 본다.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import { sourceDocKeys } from "@/api/sections";
import type { SourceDocument } from "@/api/types";
import { SourceViewer } from "@/features/preview/SourceViewer";
import "@/styles/global.css";

const PROJECT = "p1";
const SOURCE = "src-1";

// 실제 PDF 파싱 산출물의 모양 - 소제목·표·굵은 글씨가 섞인 마크다운.
const TABLE_CHUNK = [
  "## 3.2 품목별 수출 영향",
  "",
  "**철강·알루미늄**이 대EU 수출액의 대부분을 차지한다. 전환기간 종료 후 인증서",
  "구매 의무가 더해지면 품목별 부담은 아래와 같이 갈린다.",
  "",
  "| 품목 | 수출액(억 달러) | 비중 | 추가 부담(추정) |",
  "| --- | ---: | ---: | ---: |",
  "| 철강 | 43 | 7.4% | 2.1억 달러 |",
  "| 알루미늄 | 18 | 3.1% | 0.6억 달러 |",
  "| 시멘트 | 4 | 0.7% | 0.1억 달러 |",
  "",
  "> 주: 2025년 EU ETS 평균가 91유로 기준으로 환산했다.",
].join("\n");

const DOC: SourceDocument = {
  source_id: SOURCE,
  title: "EU CBAM 이행법령 영향분석 (2025)",
  url: "https://example.org/cbam-2025",
  source_type: "upload",
  chunks: [
    {
      chunk_id: "c-1",
      chunk_index: 0,
      header_path: ["3. 산업 영향"],
      page: 12,
      content: [
        "### 3.1 제도 개요",
        "",
        "CBAM은 2026년 본격 시행과 함께 수입품에 내재된 탄소배출량 신고를 의무화한다.",
        "전환기간에는 *보고 의무*만 부과된다.",
      ].join("\n"),
    },
    {
      chunk_id: "c-2",
      chunk_index: 1,
      header_path: ["3. 산업 영향"],
      page: 13,
      content: TABLE_CHUNK,
    },
    {
      chunk_id: "c-3",
      chunk_index: 2,
      header_path: ["4. 대응 방향"],
      page: 14,
      content: [
        "## 4. 대응 방향",
        "",
        "- 실측 기반 배출량 산정 체계로 전환한다",
        "- 공급망 협력사의 데이터 확보를 지원한다",
      ].join("\n"),
    },
  ],
};

// 강조할 대목 - 표 앞 문단의 "인증서 구매 의무가 더해지면"(문자 오프셋 기준).
const NEEDLE = "인증서\n구매 의무가 더해지면";
const START = TABLE_CHUNK.indexOf(NEEDLE);

function App() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(sourceDocKeys.detail(PROJECT, SOURCE), DOC);
  const location = {
    sourceId: SOURCE,
    chunkId: "c-2",
    start: START,
    end: START + NEEDLE.length,
  };
  return (
    <QueryClientProvider client={client}>
      <div className="min-h-screen bg-bg-secondary p-6">
        <h1 className="mb-4 text-sm font-semibold text-fg">원문 뷰어 확인</h1>
        <p className="mb-4 max-w-3xl text-xs text-fg-secondary">
          왼쪽은 기본(렌더), 오른쪽은 '원문 그대로'를 누른 상태여야 합니다. 두 쪽 모두 같은
          대목("인증서 구매 의무가 더해지면")이 노랗게 강조돼야 합니다.
        </p>
        <div className="flex flex-wrap gap-6">
          <div className="w-[420px] rounded border border-border bg-bg p-3">
            <SourceViewer projectId={PROJECT} location={location} />
          </div>
        </div>
      </div>
    </QueryClientProvider>
  );
}

createRoot(document.getElementById("root") as HTMLElement).render(<App />);
