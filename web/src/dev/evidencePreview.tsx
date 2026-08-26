// 근거 패널 육안 확인용 하네스 - 앱 로그인·API 없이 BlockEvidence만 실제 CSS로 그린다.
// react-query 캐시에 응답을 직접 심어 네트워크 없이 렌더한다.
// 실제 데이터의 지저분한 경우(대목 못 찾음·인용 표기 없음·근거 없는 수치·웹/파일 출처)를
// 한 화면에 모아, 걷어낸 뒤에도 필요한 정보가 남았는지 눈으로 본다.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createRoot } from "react-dom/client";
import { sectionKeys } from "@/api/sections";
import type { SectionEvidence } from "@/api/types";
import { BlockEvidence } from "@/features/preview/EvidencePanel";
import { MarkdownContent } from "@/features/preview/MarkdownContent";
import "@/styles/global.css";

const PROJECT = "p1";
const SECTION = "s1";

const BLOCK = [
  "□ EU CBAM은 2026년 본격 시행과 함께 수입품에 내재된 탄소배출량 신고를 의무화한다 (출처 3).",
  "전환기간 동안에는 보고 의무만 부과되며, 인증서 구매는 2026년부터 시작된다 [7].",
  "국내 철강업계의 대EU 수출 물량은 연간 320만 톤으로 추정된다 (출처 3).",
  "이 제도는 향후 다른 품목으로도 확대될 전망이다.",
  // 종결형이 아니라 주장으로 안 잡히는 줄 - "대조 안 함" 회색 점선 확인용.
  "적용 품목 확대와 인증서 가격 산정 방식은 이행법령에서 구체화 예정",
].join("\n\n");

const EVIDENCE: SectionEvidence = {
  section_id: SECTION,
  items: [
    {
      chunk_id: "c-1",
      source_id: "src-1",
      number: 3,
      cited: true,
      source_title: "관세청 「2026년 탄소국경조정제도(CBAM) 대응 안내서」",
      url: "https://example.org/cbam-guide",
      header_path: ["II. 제도 개요", "2.1 적용 대상"],
      content: "CBAM은 2026년 1월 1일부터 본격 시행되며 …",
    },
    {
      chunk_id: "c-2",
      source_id: "src-2",
      number: 7,
      cited: true,
      source_title: "European Commission, CBAM Implementing Regulation 2023/1773",
      url: null,
      header_path: [],
      content: "During the transitional period …",
    },
  ] as SectionEvidence["items"],
  claims: [
    {
      claim: "EU CBAM은 2026년 본격 시행과 함께 수입품에 내재된 탄소배출량 신고를 의무화한다",
      numbers: [3],
      status: "aligned",
      chunk_id: "c-1",
      span_start: 12,
      span_end: 88,
      span_text:
        "CBAM은 2026년 1월 1일부터 본격 시행되며, 수입자는 수입품에 내재된 탄소배출량을 매 분기 신고해야 한다.",
      score: 0.82,
      ungrounded: [],
      grounded: [],
    },
    {
      claim: "전환기간 동안에는 보고 의무만 부과되며, 인증서 구매는 2026년부터 시작된다",
      numbers: [7],
      status: "crosslingual",
      chunk_id: "c-2",
      span_start: null,
      span_end: null,
      span_text: null,
      score: 0.11,
      ungrounded: [],
      grounded: [],
    },
    {
      claim: "국내 철강업계의 대EU 수출 물량은 연간 320만 톤으로 추정된다",
      numbers: [3],
      status: "weak",
      chunk_id: "c-1",
      span_start: 210,
      span_end: 260,
      span_text: "대EU 철강 수출은 최근 3년간 증가세를 보이고 있다.",
      score: 0.31,
      ungrounded: ["320만"],
      grounded: [],
    },
    {
      claim: "이 제도는 향후 다른 품목으로도 확대될 전망이다",
      numbers: [],
      status: "uncited",
      chunk_id: null,
      span_start: null,
      span_end: null,
      span_text: null,
      score: 0,
      ungrounded: [],
      grounded: [],
    },
  ] as SectionEvidence["claims"],
  aligned_count: 1,
  weak_count: 1,
  unmatched_count: 0,
  pool_size: 24,
  cited_count: 2,
  unused_count: 22,
  uncited_count: 1,
  uncited_samples: [],
  uncovered: ["적용 품목 확대와 인증서 가격 산정 방식은 이행법령에서 구체화 예정"],
  traceable: true,
} as SectionEvidence;

const CITATIONS = [
  {
    number: 3,
    title: "관세청 「2026년 CBAM 대응 안내서」",
    url: "https://example.org/cbam-guide",
    source_id: "src-1",
    reliability: "high",
  },
  {
    number: 7,
    title: "European Commission, CBAM Implementing Regulation 2023/1773",
    url: null,
    source_id: "src-2",
    reliability: "medium",
  },
] as never;

const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
qc.setQueryData(sectionKeys.evidence(PROJECT, SECTION), EVIDENCE);

function App() {
  return (
    <QueryClientProvider client={qc}>
      <div className="min-h-screen bg-bg p-8 text-fg">
        <h1 className="mb-1 text-lg font-semibold">근거 패널 확인</h1>
        <p className="mb-4 text-xs text-fg-tertiary">
          드로어 폭(384px)에서 - 문장 → 출처 → 참고한 대목만 남았는지 본다.
        </p>
        <div className="flex flex-wrap items-start gap-6">
          <div>
            <p className="mb-2 text-xs font-medium text-fg-secondary">
              본문 - 표기 없는 문장에 점선(마우스를 올리면 근거 카드)
            </p>
            <div className="w-[560px] rounded border border-border p-4">
              <MarkdownContent
                content={BLOCK}
                citations={CITATIONS}
                evidence={EVIDENCE.items}
                claims={EVIDENCE.claims}
                uncovered={EVIDENCE.uncovered}
              />
            </div>
            <p className="mb-2 mt-4 text-xs font-medium text-fg-secondary">
              인용 문장도 표시 켬 - 인용=파랑, 표기 없음=주황
            </p>
            <div className="w-[560px] rounded border border-border p-4">
              <MarkdownContent
                content={BLOCK}
                citations={CITATIONS}
                evidence={EVIDENCE.items}
                claims={EVIDENCE.claims}
                uncovered={EVIDENCE.uncovered}
                markCited
              />
            </div>
          </div>
          <div>
            <p className="mb-2 text-xs font-medium text-fg-secondary">근거 패널(드로어 384px)</p>
            <div className="w-[384px] rounded border border-border p-4">
              <BlockEvidence
                projectId={PROJECT}
                sectionId={SECTION}
                blocks={[BLOCK]}
                onLocate={(loc) => console.log("locate", loc)}
              />
            </div>
          </div>
        </div>
      </div>
    </QueryClientProvider>
  );
}

createRoot(document.getElementById("root") as HTMLElement).render(<App />);

// ?focus=N 이면 N번째 문장에 포커스를 줘 호버 카드를 띄운다 - 헤드리스에서 카드 자체를
// 눈으로 확인하려면 필요하다(마우스 이벤트를 못 쏘므로 useFocus 경로를 쓴다).
const focusIdx = Number(new URLSearchParams(location.search).get("focus") ?? "-1");
if (focusIdx >= 0) {
  setTimeout(() => {
    const spans = document.querySelectorAll<HTMLElement>("span.cursor-help");
    spans[focusIdx]?.focus();
  }, 500);
}
