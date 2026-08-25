import { HttpResponse, http } from "msw";
import { DEMO_PRESETS } from "@/api/mock/fixtures/presets";
import type { PresetDetail } from "@/api/presets";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

// 실계약 미러: GET /presets/{key} - 목차 편집기 초기값용 축약 골격(데모).
const DEMO_PRESET_DETAIL: Omit<PresetDetail, "id" | "name" | "desc"> = {
  domain_context: "",
  chapters: [
    {
      title: "사업 개요",
      sections: [
        {
          title: "추진 배경 및 목적",
          direction: "정책 환경 변화와 사업 필요성을 근거 중심으로 서술",
          key_points: ["상위 계획 연계", "추진 경위"],
          builds_on: [],
          agents: ["정책동향"],
        },
        {
          title: "사업 범위와 주요 내용",
          direction: "공간적·기능적 범위를 표로 정리",
          key_points: ["사업 기간", "총사업비"],
          builds_on: [],
          agents: ["RFP구조화"],
        },
      ],
    },
    {
      title: "수요 및 시장 분석",
      sections: [
        {
          title: "수요 전망",
          direction: "인구구조 변화 기반 수요 추정",
          key_points: ["고령화율 추이"],
          builds_on: [],
          agents: ["수요분석", "시장분석"],
        },
      ],
    },
  ],
};

// 실계약 미러: GET /analysts - 배정 UI 선택지(백엔드 21종 중 데모 축약).
// queries는 "{topic}"이 장·절 제목으로 치환되는 검색 질의 템플릿(미리보기용).
const DEMO_ANALYSTS = [
  {
    id: "a02",
    name: "정책동향",
    cat: "정책",
    desc: "국내외 정책 비교·동향 분석",
    pages: "8~12",
    queries: ["{topic} 정책 동향", "{topic} policy trend"],
  },
  {
    id: "a03",
    name: "시장분석",
    cat: "시장",
    desc: "시장 규모·성장률 분석",
    pages: "8~12",
    queries: ["{topic} 시장 규모 성장률"],
  },
  { id: "a11", name: "RFP구조화", cat: "기획", desc: "과업 범위 구조화", pages: "6~10" },
  {
    id: "a15",
    name: "비용편익분석",
    cat: "경제",
    desc: "B/C·NPV 산출",
    pages: "10~15",
    queries: ["{topic} 비용편익 B/C"],
  },
  { id: "a17", name: "정책분석", cat: "정책", desc: "법령·규제 영향 평가", pages: "10~15" },
  { id: "a19", name: "수요분석", cat: "시장", desc: "수요 추정·전망", pages: "8~12" },
  { id: "a21", name: "위험분석", cat: "리스크", desc: "위험 식별·대응 전략", pages: "6~10" },
  // 개인·공유 층 - 실계약대로 id는 "u-", 공유는 shared+owner_name이 선다. 이게 없으면
  // 목킹 화면에서 '내 에이전트'·'○○의 에이전트' 묶음을 아예 볼 수 없다(2026-08-25).
  {
    id: "u-11111111-1111-1111-1111-111111111111",
    name: "내가 만든 시장분석",
    cat: "시장",
    desc: "내 개인 에이전트",
    pages: "10~15",
    queries: ["{topic} 시장"],
  },
  {
    id: "u-22222222-2222-2222-2222-222222222222",
    name: "STEEP분석 (내 버전) (최재웅)",
    cat: "정책",
    desc: "최재웅이 공개한 STEEP 변형",
    pages: "8~13",
    shared: true,
    owner_name: "최재웅",
  },
  {
    id: "u-33333333-3333-3333-3333-333333333333",
    name: "공급망분석 (최재웅)",
    cat: "시장",
    desc: "최재웅이 공개한 공급망 분석",
    pages: "8~12",
    shared: true,
    owner_name: "최재웅",
  },
  {
    id: "u-44444444-4444-4444-4444-444444444444",
    name: "STEEP분석 (내 버전) (신지영)",
    cat: "정책",
    desc: "신지영이 공개한 STEEP 변형",
    pages: "8~13",
    shared: true,
    owner_name: "신지영",
  },
];

export const presetsHandlers = [
  http.get(url("presets"), () => {
    return HttpResponse.json({ data: DEMO_PRESETS }, { status: 200 });
  }),
  http.get(url("presets/:key"), ({ params }) => {
    const key = decodeURIComponent(String(params.key));
    const preset = DEMO_PRESETS.find((p) => p.id === key || p.name === key);
    if (!preset) {
      return HttpResponse.json(
        { error: { code: "PRESET_NOT_FOUND", message: "프리셋을 찾을 수 없습니다" } },
        { status: 404 },
      );
    }
    return HttpResponse.json(
      { data: { id: preset.id, name: preset.name, desc: preset.desc, ...DEMO_PRESET_DETAIL } },
      { status: 200 },
    );
  }),
  http.get(url("analysts"), () => {
    return HttpResponse.json({ data: DEMO_ANALYSTS }, { status: 200 });
  }),
];
