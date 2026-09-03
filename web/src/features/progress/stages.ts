// 파이프라인 단계 키·라벨의 단일 진실 - 표기는 진행 스테퍼(PipelineStepper) 기준.
// 화면마다 사본이 있어 라벨이 갈라졌다(StageMap이 "검증·조립"으로 어순이 뒤집힘).
// 단계별 부가 정보(사람 검토·루프 설명 등)는 각 화면이 자기 것으로 갖는다 -
// 여기는 키·라벨 쌍만 산다.
export const PIPELINE_STAGES = [
  { key: "brief", label: "설계 검토 · 확정" },
  { key: "collect", label: "자료 수집" },
  { key: "review", label: "자료 검토 · 확정" },
  { key: "index", label: "색인 · 리허설" },
  { key: "write", label: "본문 작성" },
  { key: "assemble", label: "조립 · PM 검증" },
  { key: "done", label: "완성 검토" },
] as const;

export type PipelineStageKey = (typeof PIPELINE_STAGES)[number]["key"];
