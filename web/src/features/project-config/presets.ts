import type { Preset, ProjectConfig } from "@/api/types";

export const PRESET_LABEL: Record<Preset, string> = {
  preliminary_feasibility: "예비타당성조사",
  business_review: "사업타당성검토",
  policy_research: "정책연구",
  blank: "빈 양식",
};

export const PRESET_DESCRIPTION: Record<Preset, string> = {
  preliminary_feasibility:
    "공공사업 예비타당성 보고서 양식. 비용편익·STEEP·리스크 분석 + 검증 도구 풀세트.",
  business_review: "민간·공공 사업타당성 검토 양식. SWOT·5 Forces·비용편익 위주.",
  policy_research: "정책 연구·동향 보고서 양식. STEEP·PESTLE 위주, 출처 추적 강조.",
  blank: "분석 도구·차별화 기능을 모두 끈 빈 시작점. 직접 옵션 구성.",
};

const COMMON_SOURCES = { use_library: true, use_upload: true, use_web_search: true } as const;

export const PRESET_DEFAULTS: Record<Preset, ProjectConfig> = {
  preliminary_feasibility: {
    preset: "preliminary_feasibility",
    sources: { ...COMMON_SOURCES },
    enabled_analyzers: ["STEEP", "RISK", "COST_BENEFIT"],
    enable_pre_reconciliation: true,
    enable_consistency_graph: true,
    enable_dual_track_search: true,
    enable_source_tagging: true,
    enable_critic_agent: false,
    enable_glossary: false,
    depth_mode: "full_report",
    output_formats: ["hwpx", "pdf"],
    notification_channels: ["email"],
  },
  business_review: {
    preset: "business_review",
    sources: { ...COMMON_SOURCES },
    enabled_analyzers: ["SWOT", "FIVE_FORCES", "COST_BENEFIT", "RISK"],
    enable_pre_reconciliation: true,
    enable_consistency_graph: true,
    enable_dual_track_search: true,
    enable_source_tagging: true,
    enable_critic_agent: false,
    enable_glossary: false,
    depth_mode: "full_report",
    output_formats: ["hwpx", "pdf"],
    notification_channels: ["email"],
  },
  policy_research: {
    preset: "policy_research",
    sources: { ...COMMON_SOURCES },
    enabled_analyzers: ["STEEP", "PESTLE"],
    enable_pre_reconciliation: true,
    enable_consistency_graph: false,
    enable_dual_track_search: true,
    enable_source_tagging: true,
    enable_critic_agent: false,
    enable_glossary: true,
    depth_mode: "standard",
    output_formats: ["hwpx", "markdown"],
    notification_channels: ["email"],
  },
  blank: {
    preset: "blank",
    sources: { use_library: false, use_upload: true, use_web_search: false },
    enabled_analyzers: [],
    enable_pre_reconciliation: false,
    enable_consistency_graph: false,
    enable_dual_track_search: false,
    enable_source_tagging: false,
    enable_critic_agent: false,
    enable_glossary: false,
    depth_mode: "outline_only",
    output_formats: ["markdown"],
    notification_channels: ["email"],
  },
};
