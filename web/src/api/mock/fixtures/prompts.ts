import type { PersonalPrompt, SystemPrompt } from "@/api/prompts";
import type { LibraryNode } from "@/api/types";

// 데모 시스템 카탈로그(축약) - 실백엔드는 에이전트 21 + 작성 규칙 6종.
export const SYSTEM_PROMPTS: SystemPrompt[] = [
  {
    ref: "a01",
    kind: "agent",
    sections: {},
    name: "STEEP분석",
    content:
      "너는 STEEP 분석 전문가다. 사회·기술·경제·환경·정치 관점에서 거시 환경을 구조적으로 분석하고, 각 요인이 대상 주제에 미치는 영향을 근거와 함께 개조식으로 정리한다.",
    cat: "환경분석",
    description: "거시환경 STEEP",
  },
  {
    ref: "a07",
    kind: "agent",
    sections: {},
    name: "SWOT분석",
    content: "너는 SWOT 분석가다. 강점·약점·기회·위협을 도출하고 SO/WO/ST/WT 전략을 제시한다.",
    cat: "전략",
    description: "SWOT",
  },
  {
    ref: "a15",
    kind: "agent",
    sections: {},
    name: "비용편익분석",
    content: "너는 비용편익분석가다. 비용과 편익을 정량화해 B/C 비율과 순현재가치를 산출한다.",
    cat: "경제성",
    description: "비용편익",
  },
  {
    ref: "agent_writing_style",
    kind: "rule",
    sections: {},
    name: "agent_writing_style",
    content: "개조식·간결·근거 중심으로 작성한다. 한 문장 한 논지, 수식어 최소화.",
  },
  {
    ref: "agent_source_rules",
    kind: "rule",
    sections: {},
    name: "agent_source_rules",
    content: "모든 사실 주장에는 출처를 명시한다. 추정과 사실을 구분해 표기한다.",
  },
];

// 개인 프롬프트 인메모리 스토어(가변) - 핸들러가 CRUD로 조작한다.
export const PERSONAL_PROMPTS: PersonalPrompt[] = [
  {
    id: "up_demo_1",
    kind: "agent",
    name: "우리회사 시장분석가",
    content: "우리 회사 제품 라인 맥락에서 시장 규모·성장률·경쟁구도를 분석한다.",
    base_ref: null,
    cat: "커스텀",
    description: "사내 전용",
    spec: { volume: "normal", queries: [], sections: {} },
    is_public: false,
    updated_at: new Date("2026-05-20T00:00:00Z").toISOString(),
  },
];

let seq = 100;
export const nextPromptId = (): string => `up_${String(++seq).padStart(3, "0")}`;

/** PromptRef 마커를 단 가상 파일 노드. */
export function promptNode(p: {
  nodeId: string;
  name: string;
  scope: "personal" | "system";
  kind: "agent" | "rule";
  ref: string;
  editable: boolean;
  registeredAt?: string;
}): LibraryNode {
  return {
    id: p.nodeId,
    name: p.name,
    type: "file",
    virtual: true,
    prompt: { scope: p.scope, kind: p.kind, ref: p.ref, editable: p.editable },
    file_meta: {
      size_bytes: 0,
      registered_at: p.registeredAt ?? new Date("2026-01-01T00:00:00Z").toISOString(),
      registered_by: p.scope === "system" ? "시스템" : "나",
      source_kind: "library",
      visible_to_roles: ["viewer", "worker", "admin", "super_admin"],
    },
  };
}

export function personalPromptNode(p: PersonalPrompt): LibraryNode {
  return promptNode({
    nodeId: `uprompt-${p.id}`,
    name: p.name,
    scope: "personal",
    kind: p.kind,
    ref: p.id,
    editable: true,
    registeredAt: p.updated_at,
  });
}

export function systemPromptNode(p: SystemPrompt): LibraryNode {
  return promptNode({
    nodeId: `${p.kind === "agent" ? "sysagent" : "syscomp"}-${p.ref}`,
    name: p.name,
    scope: "system",
    kind: p.kind,
    ref: p.ref,
    editable: false,
  });
}
