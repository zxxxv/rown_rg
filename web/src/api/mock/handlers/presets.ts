import { HttpResponse, http } from "msw";
import { DEMO_PRESETS } from "@/api/mock/fixtures/presets";
import type { PresetDetail } from "@/api/presets";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

// 실계약 미러: GET /presets/{key} — 목차 편집기 초기값용 축약 골격(데모).
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
          agents: ["정책동향"],
        },
        {
          title: "사업 범위와 주요 내용",
          direction: "공간적·기능적 범위를 표로 정리",
          key_points: ["사업 기간", "총사업비"],
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
          agents: ["수요분석", "시장분석"],
        },
      ],
    },
  ],
};

// 실계약 미러: GET /analysts — 배정 UI 선택지(백엔드 21종 중 데모 축약).
const DEMO_ANALYSTS = [
  { id: "a02", name: "정책동향", cat: "정책", desc: "국내외 정책 비교·동향 분석", pages: "8~12" },
  { id: "a03", name: "시장분석", cat: "시장", desc: "시장 규모·성장률 분석", pages: "8~12" },
  { id: "a11", name: "RFP구조화", cat: "기획", desc: "과업 범위 구조화", pages: "6~10" },
  { id: "a15", name: "비용편익분석", cat: "경제", desc: "B/C·NPV 산출", pages: "10~15" },
  { id: "a17", name: "정책분석", cat: "정책", desc: "법령·규제 영향 평가", pages: "10~15" },
  { id: "a19", name: "수요분석", cat: "시장", desc: "수요 추정·전망", pages: "8~12" },
  { id: "a21", name: "위험분석", cat: "리스크", desc: "위험 식별·대응 전략", pages: "6~10" },
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
