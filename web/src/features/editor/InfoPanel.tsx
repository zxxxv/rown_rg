import { ExternalLink, Plus, RotateCw } from "lucide-react";
import { toast } from "sonner";
import type { EditorComponent, QAResult, QAVerdict } from "@/api/mock/fixtures/editor-sample";
import { useSourceRef } from "@/api/sections";
import { ConfidenceBadge } from "@/components/data-display/ConfidenceBadge";
import { StatusDot, type StatusKind } from "@/components/data-display/StatusDot";
import { EmptyState } from "@/components/feedback/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useRewriteDialog } from "@/features/editor/useRewriteDialog";
import { cn } from "@/lib/utils";

const QA_KIND: Record<QAVerdict, StatusKind> = {
  pending: "tertiary",
  passed: "success",
  warning: "warning",
  failed: "danger",
};

const QA_LABEL: Record<QAVerdict, string> = {
  pending: "대기",
  passed: "통과",
  warning: "주의",
  failed: "실패",
};

const COMPONENT_TYPE_LABEL: Record<EditorComponent["type"], string> = {
  paragraph: "본문 단락",
  table: "표",
  figure: "그림",
  callout: "강조",
};

export interface InfoPanelProps {
  component: EditorComponent | null;
}

export function InfoPanel({ component }: InfoPanelProps) {
  return (
    <aside className="flex h-full flex-col">
      <header className="border-b border-border px-3 py-2 text-[10px] font-medium uppercase tracking-wide text-fg-tertiary">
        선택된 컴포넌트
      </header>
      <div className="flex-1 overflow-y-auto p-3">
        {!component ? (
          <EmptyState
            title="컴포넌트를 클릭하세요"
            description="본문 영역에서 단락이나 표를 클릭하면 출처·신뢰도·QA 정보가 표시됩니다."
          />
        ) : (
          <Body component={component} />
        )}
      </div>
    </aside>
  );
}

function Body({ component }: { component: EditorComponent }) {
  const { openRewrite } = useRewriteDialog();
  return (
    <div className="flex flex-col gap-4">
      <section>
        <div className="flex items-center justify-between gap-2">
          <Badge variant="secondary" className="font-mono text-[10px]">
            {component.id}
          </Badge>
          <ConfidenceBadge value={component.confidence} />
        </div>
        <p className="mt-2 text-xs text-fg-tertiary">{COMPONENT_TYPE_LABEL[component.type]}</p>
      </section>

      <QASection qa={component.qa} />

      <section>
        <h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-fg-tertiary">
          출처 ({component.src_ids.length})
        </h4>
        <ul className="flex flex-col gap-2">
          {component.src_ids.map((srcId) => (
            <SourceItem key={srcId} srcId={srcId} />
          ))}
        </ul>
      </section>

      {component.cross_references.length > 0 ? (
        <section>
          <h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-fg-tertiary">
            연결된 섹션 ({component.cross_references.length})
          </h4>
          <ul className="flex flex-col gap-1">
            {component.cross_references.map((xref) => (
              <li
                key={xref.section_id}
                className="flex items-center gap-2 rounded border border-border bg-bg-secondary px-2 py-1 text-xs"
              >
                <span className="font-mono text-fg-tertiary">{xref.section_id}</span>
                <span className="text-fg">{xref.section_title}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <footer className="mt-2 flex flex-col gap-2 border-t border-border pt-3">
        <Button size="sm" className="w-full" onClick={() => openRewrite(component.id)}>
          <RotateCw className="mr-1 h-3.5 w-3.5" />
          재작성
        </Button>
        <Button
          size="sm"
          variant="ghost"
          className="w-full"
          onClick={() => toast("출처 추가 — 구현 예정")}
        >
          <Plus className="mr-1 h-3.5 w-3.5" />
          출처 추가
        </Button>
      </footer>
    </div>
  );
}

function QASection({ qa }: { qa: QAResult }) {
  const entries: { key: keyof QAResult; label: string }[] = [
    { key: "fact", label: "Fact" },
    { key: "consistency", label: "Consist." },
    { key: "style", label: "Style" },
    { key: "critic", label: "Critic" },
  ];
  return (
    <section>
      <h4 className="mb-2 text-xs font-medium uppercase tracking-wide text-fg-tertiary">QA 상태</h4>
      <ul className="grid grid-cols-2 gap-1.5">
        {entries.map((e) => {
          const verdict = qa[e.key];
          return (
            <li
              key={e.key}
              className={cn(
                "flex items-center justify-between rounded border border-border px-2 py-1.5",
                verdict === "warning" && "border-fg-warning/40 bg-bg-warning",
                verdict === "failed" && "border-fg-danger/40 bg-bg-danger",
              )}
            >
              <span className="text-xs text-fg">{e.label}</span>
              <StatusDot kind={QA_KIND[verdict]} label={QA_LABEL[verdict]} />
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function SourceItem({ srcId }: { srcId: string }) {
  const sourceQuery = useSourceRef(srcId);
  const source = sourceQuery.data;

  if (sourceQuery.isLoading || !source) {
    return (
      <li className="rounded border border-border bg-bg-secondary p-2 text-xs text-fg-tertiary">
        불러오는 중…
      </li>
    );
  }
  return (
    <li className="flex flex-col gap-1.5 rounded border border-border bg-bg p-2">
      <div className="flex items-start justify-between gap-2">
        <span className="line-clamp-2 text-xs font-medium text-fg">{source.title}</span>
        <ConfidenceBadge value={source.reliability} />
      </div>
      <div className="flex flex-wrap items-center gap-x-2 font-mono text-[10px] text-fg-tertiary">
        <span>{source.source}</span>
        {source.published_at ? <span>{source.published_at}</span> : null}
        {source.pages !== undefined ? <span>{source.pages}p</span> : null}
      </div>
      <button
        type="button"
        onClick={() => toast(`원본 보기 — ${source.title} (구현 예정)`)}
        className="inline-flex items-center gap-1 self-start text-[11px] text-fg-info hover:underline"
      >
        원본 보기 <ExternalLink className="h-2.5 w-2.5" />
      </button>
    </li>
  );
}
