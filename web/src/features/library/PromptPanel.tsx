import { BookOpen, Copy, ExternalLink, Wand2 } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { type PresetChapterDetail, useImportSharedPreset, usePresetDetail } from "@/api/presets";
import { useImportSharedPrompt, usePersonalPrompt, useSystemPrompt } from "@/api/prompts";
import type { PromptRef } from "@/api/types";
import { Button } from "@/components/ui/button";

const KIND_LABEL: Record<PromptRef["kind"], string> = {
  agent: "에이전트",
  rule: "작성 규칙",
  preset: "목차 프리셋",
};

/** 프롬프트 파일 노드 상세 - 라이브러리에선 읽기 전용. 편집·추가는 '프롬프트 관리' 페이지에서. */
export function PromptBody({ prompt }: { prompt: PromptRef }) {
  if (prompt.scope === "shared") return <SharedPromptView prompt={prompt} />;
  if (prompt.scope === "system") return <SystemPromptView prompt={prompt} />;
  return <PersonalPromptView prompt={prompt} />;
}

/** '내 것으로 가져오기' - 복제해야 고쳐 쓸 수 있다. 복사본은 비공개로 만들어지고,
 * 에이전트는 base_ref 없이 들어온다(남의 오버라이드가 내 시스템 항목을 갈아끼우면 안 된다). */
function ImportButton({ prompt }: { prompt: PromptRef }) {
  const importPrompt = useImportSharedPrompt();
  const importPreset = useImportSharedPreset();
  const pending = importPrompt.isPending || importPreset.isPending;
  if (!prompt.importable) return null;
  const run = async () => {
    try {
      const saved =
        prompt.kind === "preset"
          ? await importPreset.mutateAsync(prompt.ref)
          : await importPrompt.mutateAsync(prompt.ref);
      toast.success(`"${saved.name}"을(를) 내 것으로 가져왔습니다`, {
        description: "이제 고쳐 쓸 수 있습니다. 공개는 꺼진 상태입니다.",
      });
    } catch (err) {
      toast.error("가져오지 못했습니다", {
        description: err instanceof ApiError ? err.message : "다시 시도해 주세요.",
      });
    }
  };
  return (
    <Button type="button" variant="outline" size="sm" disabled={pending} onClick={() => void run()}>
      <Copy className="mr-1 h-3.5 w-3.5" aria-hidden />
      {pending ? "가져오는 중…" : "내 것으로 가져오기"}
    </Button>
  );
}

/** 남이 공개한 자산 - 라이브러리에선 읽기 전용이다. 여기서 고칠 수 있으면 공유가
 * 아니라 공용 편집이 된다. 고쳐 쓰려면 '내 것으로 가져오기'로 복제한다. */
function SharedPromptView({ prompt }: { prompt: PromptRef }) {
  // 배너+가져오기 버튼뿐이라 본문이 안 보였다(2026-08-20 지적) - 열람은 공개의 본질이라
  // 에이전트는 프롬프트 원문을, 프리셋은 목차 골격을 그대로 보여준다.
  const agentQuery = usePersonalPrompt(prompt.ref, prompt.kind !== "preset");
  const presetQuery = usePresetDetail(prompt.kind === "preset" ? `u:${prompt.ref}` : null);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 rounded border border-border bg-bg-secondary px-3 py-2 text-xs text-fg-tertiary">
        <BookOpen className="h-3.5 w-3.5" aria-hidden />
        {prompt.owner_name ? `${prompt.owner_name}님이` : "동료가"} 공개한 {KIND_LABEL[prompt.kind]}{" "}
        - 그대로 골라 쓸 수 있고, 고쳐 쓰려면 아래에서 내 것으로 가져오세요. 원본은 주인만 고칠 수
        있습니다.
      </div>
      {prompt.kind === "preset" ? (
        <PresetOutlinePreview
          loading={presetQuery.isLoading}
          error={presetQuery.isError}
          chapters={presetQuery.data?.chapters}
        />
      ) : (
        <PromptText
          loading={agentQuery.isLoading}
          error={agentQuery.isError}
          content={agentQuery.data?.content}
        />
      )}
      <ImportButton prompt={prompt} />
    </div>
  );
}

function PresetOutlinePreview({
  loading,
  error,
  chapters,
}: {
  loading: boolean;
  error: boolean;
  chapters?: PresetChapterDetail[];
}) {
  if (loading) return <p className="text-xs text-fg-tertiary">목차를 불러오는 중…</p>;
  if (error || !chapters)
    return <p className="text-xs text-fg-danger">목차를 불러오지 못했습니다</p>;
  return (
    <ol className="flex flex-col gap-1.5 rounded border border-border bg-bg p-3 text-xs">
      {chapters.map((c, i) => (
        <li key={`${c.title}:${c.sections.map((s) => s.title).join("|")}`}>
          <span className="font-medium text-fg">
            {i + 1}. {c.title}
          </span>
          <span className="ml-2 text-fg-tertiary">
            {c.sections.map((s) => s.title).join(" · ")}
          </span>
        </li>
      ))}
    </ol>
  );
}

function SystemPromptView({ prompt }: { prompt: PromptRef }) {
  // 프리셋은 프롬프트 본문이 아니라 목차 골격이다 - /prompts/system은 agent·rule만
  // 알아서 preset을 물으면 "불러오지 못했습니다"가 났다(2026-08-20 지적). 프리셋 상세
  // API(GET /presets/{key})로 골격을 그대로 보여준다.
  const isPreset = prompt.kind === "preset";
  const query = useSystemPrompt(prompt.kind, prompt.ref, !isPreset);
  const presetQuery = usePresetDetail(isPreset ? prompt.ref : null);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 rounded border border-border bg-bg-secondary px-3 py-2 text-xs text-fg-tertiary">
        <BookOpen className="h-3.5 w-3.5" aria-hidden />
        시스템 {KIND_LABEL[prompt.kind]} - 읽기 전용입니다. 내 것으로 만들려면 프롬프트 관리
        페이지에서 같은 이름으로 저장하세요.
      </div>
      {isPreset ? (
        <PresetOutlinePreview
          loading={presetQuery.isLoading}
          error={presetQuery.isError}
          chapters={presetQuery.data?.chapters}
        />
      ) : (
        <PromptText loading={query.isLoading} error={query.isError} content={query.data?.content} />
      )}
    </div>
  );
}

function PersonalPromptView({ prompt }: { prompt: PromptRef }) {
  const navigate = useNavigate();
  const query = usePersonalPrompt(prompt.ref);
  const loaded = query.data;
  // 관리자 '사용자별 자료' 미러는 editable=false로 내려온다 - 남의 자산은 열람 전용이라
  // "내 프롬프트" 문구와 편집 버튼이 거짓말이 된다.
  const readOnly = !prompt.editable;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 rounded border border-dashed border-border bg-bg-secondary px-3 py-2 text-xs text-fg-tertiary">
        <Wand2 className="h-3.5 w-3.5" aria-hidden />
        {readOnly ? "" : "내 "}
        {loaded ? KIND_LABEL[loaded.kind] : "프롬프트"}
        {loaded?.base_ref ? ` · 시스템 "${loaded.base_ref}" 덮어쓰기` : loaded ? " · 신규" : ""}
        {readOnly ? " - 열람 전용(소유자만 편집)" : " - 편집·삭제는 프롬프트 관리 페이지에서."}
      </div>
      <PromptText
        loading={query.isLoading}
        error={query.isError || !loaded}
        content={loaded?.content}
      />
      {readOnly ? null : (
        <Button variant="outline" size="sm" className="w-fit" onClick={() => navigate("/prompts")}>
          프롬프트 관리에서 편집
          <ExternalLink className="ml-1 h-3.5 w-3.5" aria-hidden />
        </Button>
      )}
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
