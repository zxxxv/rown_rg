import type { Preset, Project, ProjectConfig, ProjectStatus } from "@/api/types";

const TITLES = [
  "광역교통망 확충 사업 예비타당성조사",
  "지방 인구 고령화 대응 정책 연구",
  "재생에너지 확대 로드맵 수립",
  "스마트시티 통합 플랫폼 사업타당성 검토",
  "청년 1인가구 주거지원 사업 예비타당성조사",
  "디지털 헬스케어 거점 의료기관 사업타당성",
  "수소경제 산업단지 조성 정책 연구",
  "공공 데이터 개방 플랫폼 고도화 사업",
  "지역혁신 클러스터 조성 사업 예비타당성",
  "초중등 AI 디지털교과서 도입 정책 연구",
  "전기차 충전 인프라 확충 사업타당성 검토",
  "도시재생 뉴딜 후속 사업 예비타당성조사",
  "공공 R&D 성과 확산 정책 연구",
  "농어촌 공공의료 거점 확충 사업타당성",
  "탄소중립 산업전환 지원 정책 연구",
  "지역 관광 거점 도시 조성 예비타당성",
  "노후 산업단지 재구조화 사업타당성",
  "공공임대주택 100만호 공급 정책 연구",
  "친환경 어업 전환 지원 사업 예비타당성",
  "디지털 교과서 보급 인프라 사업타당성",
  "AI 학습 데이터 구축 사업 예비타당성",
  "지방 공항 활성화 정책 연구",
  "공공의료 인력 양성 사업타당성",
  "데이터센터 분산 입지 정책 연구",
  "스마트팜 보급 확대 사업 예비타당성",
  "공공 교통 환승체계 개편 사업타당성",
  "도서·산간 통신 인프라 확충 정책 연구",
  "지역 거점 국립대 육성 사업 예비타당성",
  "디지털 격차 해소 종합 정책 연구",
  "공공 신축 건축물 제로에너지 사업타당성",
  "재난 대응 통합관제 시스템 예비타당성",
  "수자원 재이용 인프라 사업타당성",
  "AI 반도체 산업 육성 정책 연구",
  "고령자 디지털 활용 지원 사업 예비타당성",
  "지능형 교통체계 ITS 고도화 사업타당성",
  "공공 행정망 클라우드 전환 정책 연구",
  "녹색금융 활성화 정책 연구",
  "산학 협력 거점 조성 사업 예비타당성",
  "치매 전담 공공요양시설 사업타당성",
  "메타버스 공공서비스 정책 연구",
  "지방소멸 대응 종합대책 정책 연구",
  "공공 식수 안전망 강화 사업 예비타당성",
  "AI 윤리·안전성 가이드라인 정책 연구",
  "디지털 트윈 도시 구축 사업타당성",
  "공공 도서관 네트워크 확충 예비타당성",
  "양자컴퓨팅 산업 생태계 정책 연구",
  "공공 빅데이터 거버넌스 정책 연구",
  "지방 공공기관 이전 효과 정책 연구",
  "스타트업 거점 캠퍼스 조성 예비타당성",
  "장애인 디지털 접근성 강화 정책 연구",
] as const;

const TOPICS = [
  "수도권 광역교통망의 5년간 추진 현황을 점검하고 잔여 노선의 추진 우선순위를 재정렬한다.",
  "65세 이상 인구 비중이 25%를 넘어선 시·군의 정책 수요를 정량적으로 분석한다.",
  "2030년 재생에너지 비중 30% 달성을 위한 단계별 투자 계획을 평가한다.",
  "복수 지자체가 운영 중인 스마트시티 플랫폼의 통합·연계 방안을 검토한다.",
  "수도권·지방 청년의 주거비 부담을 비교하고 지원 모델의 효과성을 분석한다.",
  "원격 진료와 지역 거점 의료기관의 결합 모델을 사업타당성 관점에서 검토한다.",
  "수소 생산·저장·활용 산업단지의 적정 규모와 입지 조건을 정책적으로 평가한다.",
  "공공 데이터 개방률은 높아졌으나 활용도가 낮은 원인을 정책적으로 진단한다.",
  "지역 특화 산업과 연계한 혁신 클러스터 후보지의 비교 평가.",
  "AI 디지털교과서 도입 1년차 성과와 보완 과제를 정책 연구로 정리한다.",
  "충전기 보급률이 낮은 광역시 외곽 지역의 인프라 보완 방안을 검토한다.",
  "1차 뉴딜 사업의 효과를 평가하고 후속 사업의 우선순위를 재정렬한다.",
  "공공 R&D 성과의 사업화 비율 정체 원인을 정책적으로 분석한다.",
  "공공의료 거점 의료기관 확충의 비용·편익을 사업타당성 관점에서 검토한다.",
  "탄소중립 전환 과정에서 영향이 큰 산업의 지원 정책 방향성을 도출한다.",
  "관광 거점 도시 후보의 인구·접근성·인프라를 종합 평가한다.",
  "산업단지의 노후화 정도와 재구조화 비용을 사업타당성 관점에서 비교한다.",
  "장기 공공임대주택 100만호 공급 목표의 실현 가능성을 정책적으로 검토한다.",
  "친환경 전환을 추진하는 어가의 경제적 부담과 지원 모델의 적정성을 평가한다.",
  "디지털 교과서 보급에 따른 통신·전력 인프라 보강 필요성을 검토한다.",
  "AI 학습용 한국어 데이터의 구축 비용과 활용 가치를 정량적으로 분석한다.",
  "이용률이 낮은 지방 공항의 활성화 방안과 노선 재편의 효과를 검토한다.",
  "공공의료 인력 부족 지역의 양성 사업의 비용·편익을 분석한다.",
  "수도권 집중도가 높은 데이터센터의 지방 분산 입지 인센티브를 검토한다.",
  "스마트팜 보급 확대의 농가 소득 효과와 기술 보급 모델을 평가한다.",
  "환승 시간·요금 체계의 통합 개편이 통행자에게 미치는 영향을 분석한다.",
  "도서·산간 지역의 5G·해저케이블 인프라 확충 필요성을 정책적으로 검토한다.",
  "지방 거점 국립대 육성 사업의 효과성과 사업 모델을 평가한다.",
  "고령자·장애인·저소득층의 디지털 격차를 정량 분석하고 해소 정책을 제안한다.",
  "공공 신축 건축물의 ZEB(제로에너지) 인증 의무화의 경제성을 검토한다.",
  "재난 통합관제 시스템 구축의 비용·편익과 운영 모델을 검토한다.",
  "물 부족 대응을 위한 재이용수 공급망 구축의 사업타당성을 평가한다.",
  "AI 반도체 산업의 글로벌 경쟁력 확보를 위한 정책 방향을 도출한다.",
  "고령자의 디지털 기기·서비스 활용을 돕는 종합지원 사업의 효과성을 검토한다.",
  "지능형 교통체계의 차세대 기술(C-ITS) 도입의 사업타당성을 평가한다.",
  "행정망의 민간 클라우드 전환 가능성과 보안 요건을 정책적으로 분석한다.",
  "녹색금융 활성화를 위한 공적 금융기관의 역할 재정립을 검토한다.",
  "대학·연구소·기업이 모인 산학 협력 거점 조성의 효과성을 평가한다.",
  "치매 전담 공공요양시설 확충의 비용·편익과 운영 모델을 분석한다.",
  "메타버스·확장현실 기반 공공서비스의 적용 분야와 기대 효과를 검토한다.",
  "지방소멸 위험지역의 인구·고용 추세를 분석하고 종합대책을 도출한다.",
  "공공 식수 공급망의 노후화 진단과 보강 사업의 사업타당성을 검토한다.",
  "AI 기술의 윤리·안전성에 관한 정책 가이드라인을 비교 분석한다.",
  "도시 단위 디지털 트윈 플랫폼 구축의 비용과 활용 가치를 검토한다.",
  "공공 도서관 네트워크의 균형 발전을 위한 확충 사업의 사업타당성을 평가한다.",
  "양자컴퓨팅 산업의 인재·인프라 정책의 단기·중기 추진 방향을 분석한다.",
  "공공 빅데이터의 거버넌스 모델과 데이터 활용 정책을 정리한다.",
  "공공기관 지방이전의 지역경제 효과와 후속 정책 방향을 분석한다.",
  "스타트업 거점 캠퍼스 조성 사업의 비용·편익과 운영 모델을 평가한다.",
  "장애인의 디지털 접근성 강화 정책의 실효성을 진단하고 보완책을 제시한다.",
] as const;

const STATUS_POOL: ProjectStatus[] = [
  "writing",
  "writing",
  "writing",
  "writing",
  "reviewing",
  "reviewing",
  "completed",
  "completed",
  "archived",
  "created",
];
const PRESET_POOL: Preset[] = [
  "preliminary_feasibility",
  "preliminary_feasibility",
  "business_review",
  "policy_research",
  "blank",
];

const DEFAULT_CONFIG: ProjectConfig = {
  preset: "preliminary_feasibility",
  sources: { use_library: true, use_upload: true, use_web_search: true },
  model_mode: "standard",
  enabled_analyzers: ["STEEP", "COST_BENEFIT"],
  enable_pre_reconciliation: true,
  enable_consistency_graph: true,
  enable_dual_track_search: true,
  enable_source_tagging: true,
  enable_critic_agent: false,
  enable_glossary: false,
  depth_mode: "full_report",
  output_formats: ["hwpx"],
  notification_channels: ["email"],
};

const NOW = new Date("2026-05-27T00:00:00Z");

function offsetDate(days: number): string {
  const d = new Date(NOW);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString();
}

function pickProgress(status: ProjectStatus, i: number): number {
  if (status === "completed" || status === "archived") return 100;
  if (status === "created") return 0;
  if (status === "reviewing") return 80 + (i % 15);
  return 10 + ((i * 13) % 75);
}

const W4_DEMO_PROJECT: Project = {
  id: "proj_demo_w4",
  title: "OO지역 SOC 사업 예비타당성 검토",
  topic:
    "OO지역의 광역교통망 확충 사업의 예비타당성을 평가한다. 비용편익·정책성·지역균형을 종합적으로 검토하며, 인구 고령화·수도권 집중도·잠재성장률 변화가 사업 수요에 미치는 영향을 분석한다.",
  preset: "preliminary_feasibility",
  config: {
    preset: "preliminary_feasibility",
    sources: { use_library: true, use_upload: true, use_web_search: true },
    model_mode: "standard",
    enabled_analyzers: ["STEEP", "COST_BENEFIT", "RISK"],
    enable_pre_reconciliation: true,
    enable_consistency_graph: true,
    enable_dual_track_search: true,
    enable_source_tagging: true,
    enable_critic_agent: false,
    enable_glossary: false,
    depth_mode: "full_report",
    output_formats: ["hwpx"],
    notification_channels: ["email"],
  },
  status: "writing",
  depth_mode: "full_report",
  owner_id: "u_admin_001",
  owner_name: "관리자",
  created_at: offsetDate(-7),
  updated_at: offsetDate(0),
  progress: 67,
};

export const DEMO_PROJECTS: Project[] = [
  W4_DEMO_PROJECT,
  ...TITLES.map((title, i) => {
    const status = STATUS_POOL[i % STATUS_POOL.length];
    const preset = PRESET_POOL[i % PRESET_POOL.length];
    return {
      id: `proj_${String(i + 1).padStart(3, "0")}`,
      title,
      topic: TOPICS[i % TOPICS.length],
      preset,
      config: { ...DEFAULT_CONFIG, preset },
      status,
      depth_mode: "full_report" as const,
      owner_id: "u_admin_001",
      owner_name: "관리자",
      created_at: offsetDate(-(i * 2 + 1)),
      updated_at: offsetDate(-(i % 5)),
      progress: pickProgress(status, i),
    };
  }),
];
