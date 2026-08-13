import type { PresetRead } from "@/api/presets";

// 실계약 형태: 백엔드 src/prompts/presets 카탈로그(5종)와 동일한 id/name.
export const DEMO_PRESETS: PresetRead[] = [
  {
    id: "예비타당성조사",
    name: "예비타당성조사",
    desc: "7챕터 35섹션",
    n_chapters: 7,
    n_sections: 35,
    scope: "system",
  },
  {
    id: "정책기획보고서",
    name: "정책기획보고서",
    desc: "6챕터 17섹션",
    n_chapters: 6,
    n_sections: 17,
    scope: "system",
  },
  {
    id: "경영컨설팅보고서",
    name: "경영컨설팅보고서",
    desc: "6챕터 18섹션",
    n_chapters: 6,
    n_sections: 18,
    scope: "system",
  },
  {
    id: "산업동향보고서",
    name: "산업동향보고서",
    desc: "6챕터 11섹션",
    n_chapters: 6,
    n_sections: 11,
    scope: "system",
  },
  {
    id: "조사분석보고서",
    name: "조사분석보고서",
    desc: "6챕터 14섹션",
    n_chapters: 6,
    n_sections: 14,
    scope: "system",
  },
];
