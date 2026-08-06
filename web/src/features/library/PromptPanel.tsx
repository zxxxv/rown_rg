import { BookOpen, ExternalLink, Wand2 } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { type PromptKind, usePersonalPrompt, useSystemPrompt } from "@/api/prompts";
import type { PromptRef } from "@/api/types";
import { Button } from "@/components/ui/button";

const KIND_LABEL: Record<PromptKind, string> = { agent: "에이전트", rule: "작성 규칙" };

/** 프롬프트 파일 노드 상세 — 라이브러리에선 읽기 전용. 편집·추가는 '프롬프트 관리' 페이지에서. */
export function PromptBody({ prompt }: { prompt: PromptRef }) {
  if (prompt.scope === "system") return <SystemPromptView prompt={prompt} />;
  return <PersonalPromptView prompt={prompt} />;
}

function SystemPromptView({ prompt }: { prompt: PromptRef }) {
  const query = useSystemPrompt(prompt.kind, prompt.ref);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 rounded border border-border bg-bg-secondary px-3 py-2 text-xs text-fg-tertiary">
        <BookOpen className="h-3.5 w-3.5" aria-hidden />
        시스템 {KIND_LABEL[prompt.kind]} - 읽기 전용입니다. 내 것으로 만들려면 프롬프트 관리
        페이지에서 같은 이름으로 저장하세요.
      </div>
      <PromptText loading={query.isLoading} error={query.isError} content={query.data?.content} />
    </div>
  );
}

function PersonalPromptView({ prompt }: { prompt: PromptRef }) {
  const navigate = useNavigate();
  const query = usePersonalPrompt(prompt.ref);
  const loaded = query.data;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 rounded border border-dashed border-border bg-bg-secondary px-3 py-2 text-xs text-fg-tertiary">
        <Wand2 className="h-3.5 w-3.5" aria-hidden />내{" "}
        {loaded ? KIND_LABEL[loaded.kind] : "프롬프트"}
        {loaded?.base_ref ? ` · 시스템 "${loaded.base_ref}" 덮어쓰기` : loaded ? " · 신규" : ""} -
        편집·삭제는 프롬프트 관리 페이지에서.
      </div>
      <PromptText
        loading={query.isLoading}
        error={query.isError || !loaded}
        content={loaded?.content}
      />
      <Button variant="outline" size="sm" className="w-fit" onClick={() => navigate("/prompts")}>
        프롬프트 관리에서 편집
        <ExternalLink className="ml-1 h-3.5 w-3.5" aria-hidden />
      </Button>
    </div>
  );
}

function PromptText({
  loading,
  error,
  content,
}: {
  loading: boolean;
  error: boolean;
  content: string | undefined;
}) {
  if (loading) return <p className="text-sm text-fg-tertiary">불러오는 중…</p>;
  if (error) return <p className="text-sm text-fg-danger">프롬프트를 불러오지 못했습니다.</p>;
  return (
    <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded border border-border bg-bg p-3 font-mono text-xs leading-relaxed text-fg-secondary">
      {content}
    </pre>
  );
}
