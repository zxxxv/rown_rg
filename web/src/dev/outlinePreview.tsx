// 개조식 내어쓰기 육안·실측 확인용 하네스 - 앱 로그인·API 없이 MarkdownContent만
// 실제 CSS로 그린다. 좁은 폭에서 줄이 넘어가야 어긋남이 보이므로 폭을 좁혀 둔다.
// 컴파일된다 != 제대로 그려진다(차트 하네스와 같은 판단, 2026-08-11).
import { createRoot } from "react-dom/client";
import { MarkdownContent } from "@/features/preview/MarkdownContent";
import "@/styles/global.css";

const MD = [
  "□ 대주제 줄이 길어져 두 줄 이상으로 넘어가는 경우를 본다 - 둘째 줄은 □ 아래가 아니라 '대'자 글머리에 맞아야 한다",
  "",
  "ㅇ 하위 항목도 같은 규칙이다. 이 문장은 일부러 길게 늘여서 반드시 줄이 넘어가게 만든다 - 둘째 줄은 'ㅇ' 아래가 아니라 '하'자에 맞아야 한다",
  "",
  "○ 원 마커 변형도 같은 자리에 맞아야 한다. 이 문장 역시 두 줄 이상으로 넘어가도록 충분히 길게 쓴다",
  "",
  "◦ 작은 원 마커 변형. 이 문장 역시 두 줄 이상으로 넘어가도록 충분히 길게 늘여 쓴 문장이다",
  "",
  "ㅇ 인용 마커가 섞인 줄도 확인한다[1]. 배지가 들어가도 내어쓰기가 깨지면 안 되고 줄바꿈도 자연스러워야 한다[2]",
  "",
  "- 마크다운 목록은 원래대로 ul 절대배치를 쓴다. 이 항목도 길게 늘여 두 줄로 넘겨 본다",
  "",
  "평범한 문단은 글머리가 없으니 내어쓰기도 없다. 이 문장은 비교용으로 길게 늘여 둔다.",
  "",
  "출처: 관세청 「2026년 탄소국경조정제도 대응 안내서」; 환경부 「국가 온실가스 인벤토리 보고서」; European Commission, CBAM Implementing Regulation 2023/1773; 산업통상자원부 보도자료",
].join("\n");

function App() {
  return (
    <div className="min-h-screen bg-bg p-8 text-fg">
      <h1 className="mb-4 text-lg font-semibold">개조식 내어쓰기 확인</h1>
      {/* 좁은 폭 - 줄이 넘어가야 둘째 줄 정렬을 볼 수 있다 */}
      <div id="narrow" className="w-[420px] rounded border border-border p-4">
        <MarkdownContent content={MD} />
      </div>
      <pre id="measure" className="mt-6 whitespace-pre-wrap text-xs text-fg-secondary" />
    </div>
  );
}

createRoot(document.getElementById("root") as HTMLElement).render(<App />);

/** 실측 - 줄바꿈된 본문의 각 줄 왼쪽 x가 같은지. 다르면 내어쓰기가 깨진 것이다.
 * 눈으로도 보지만 픽셀 판정은 사람이 못 한다(2026-08-11 차트에서 배운 것). */
setTimeout(() => {
  const out: string[] = [];
  for (const p of document.querySelectorAll<HTMLElement>("#narrow p, #narrow li")) {
    const body = (p.querySelector(":scope > span:last-child") ?? p) as HTMLElement;
    // 본문 칸은 flex item(블록 박스)이라 getClientRects()가 한 덩이로 나온다 -
    // 줄 단위 상자는 Range로 떠야 보인다.
    const range = document.createRange();
    range.selectNodeContents(body);
    const rects = Array.from(range.getClientRects()).filter((r) => r.width > 1 && r.height > 1);
    if (rects.length < 2) continue;
    const lefts = rects.map((r) => Math.round(r.left * 10) / 10);
    const spread = Math.max(...lefts) - Math.min(...lefts);
    out.push(
      `${spread < 0.5 ? "OK  " : "FAIL"} lines=${rects.length} lefts=[${lefts.join(", ")}] :: ${(p.textContent ?? "").slice(0, 28)}`,
    );
  }
  const el = document.getElementById("measure");
  if (el) el.textContent = out.join("\n") || "(줄바꿈된 줄 없음 - 폭을 더 좁혀라)";
}, 600);
