import { ArrowLeft, ArrowRight, CheckCircle2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { useProjectContradictions, useResolveContradiction } from "@/api/contradictions";
import type { Contradiction, ContradictionWinner } from "@/api/types";
import { StatusDot, type StatusKind } from "@/components/data-display/StatusDot";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ContradictionCompare } from "@/features/source-review/ContradictionCompare";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";

const WINNER_TOAST: Record<ContradictionWinner, string> = {
  A: "자료 A로 결정되었습니다.",
  B: "자료 B로 결정되었습니다.",
  defer: "보류로 결정되었습니다. 작성 단계에서 다시 검토합니다.",
};

export default function ReconcilePage() {
  const { id: projectId = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { user, logout } = useAuth();

  const contradictionsQuery = useProjectContradictions(projectId);
  const resolve = useResolveContradiction();

  const items = useMemo(
    () => contradictionsQuery.data?.items ?? [],
    [contradictionsQuery.data?.items],
  );

  const counts = useMemo(() => {
    let pending = 0;
    let resolved = 0;
    for (const c of items) {
      if (c.status === "pending") pending++;
      else resolved++;
    }
    return { pending, resolved, total: items.length };
  }, [items]);

  const sortedItems = useMemo(
    () =>
      [...items].sort((a, b) => {
        if (a.status !== b.status) return a.status === "pending" ? -1 : 1;
        return a.created_at.localeCompare(b.created_at);
      }),
    [items],
  );

  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    if (!selectedId && sortedItems[0]) {
      setSelectedId(sortedItems[0].id);
    }
  }, [selectedId, sortedItems]);

  const selected = sortedItems.find((c) => c.id === selectedId) ?? null;

  const handleResolve = (winner: ContradictionWinner) => {
    if (!selected) return;
    const currentId = selected.id;
    resolve.mutate(
      { projectId, cid: currentId, winner },
      {
        onSuccess: () => {
          toast.success(WINNER_TOAST[winner]);
          const nextPending = sortedItems.find((c) => c.id !== currentId && c.status === "pending");
          if (nextPending) setSelectedId(nextPending.id);
        },
        onError: (err) => {
          const msg = err instanceof ApiError ? err.message : "결정 처리에 실패했습니다.";
          toast.error("롤백됨", { description: msg });
        },
      },
    );
  };

  const allResolved = counts.total > 0 && counts.pending === 0;

  return (
    <AppShell
      user={user ? { name: user.name, role: user.role } : null}
      onLogout={() => void logout()}
      tokenUsage={{ used: 1_240_000, limit: 5_000_000 }}
    >
      <div className="flex flex-col gap-4">
        <Button
          variant="ghost"
          size="sm"
          className="w-fit text-fg-secondary"
          onClick={() => navigate(`/projects/${projectId}/overview`)}
        >
          <ArrowLeft className="mr-1 h-4 w-4" />
          프로젝트 개요
        </Button>

        <header className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <h1 className="text-3xl font-semibold text-fg">자료 모순 해결</h1>
            <Badge variant="secondary" className="font-mono">
              검토 지점 #2
            </Badge>
          </div>
          <div className="flex flex-wrap items-center gap-3 font-mono text-sm">
            <span className="text-fg-warning">미해결 {counts.pending}</span>
            <span className="text-fg-success">해결 {counts.resolved}</span>
            <span className="text-fg-tertiary">전체 {counts.total}</span>
          </div>
          <p className="rounded border border-border-info bg-bg-info px-3 py-2 text-xs text-fg-info">
            본 화면은 Phase 4에서 실작동 전환됩니다. 현재는 모킹된 데이터로 결정 흐름을 시연합니다.
          </p>
        </header>

        {contradictionsQuery.isLoading ? (
          <LoadingSkeleton variant="block" />
        ) : items.length === 0 ? (
          <EmptyState
            icon={CheckCircle2}
            title="모순 없음"
            description="모든 자료가 일관됩니다. 다음 단계로 진행할 수 있습니다."
            action={
              <Button onClick={() => navigate(`/projects/${projectId}/progress`)}>
                진행 패널로 <ArrowRight className="ml-1 h-4 w-4" />
              </Button>
            }
          />
        ) : (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
            <ContradictionList
              items={sortedItems}
              selectedId={selectedId}
              onSelect={setSelectedId}
            />

            <main className="rounded border border-border bg-bg p-5">
              {allResolved ? (
                <AllResolvedView
                  total={counts.total}
                  onNext={() => navigate(`/projects/${projectId}/progress`)}
                />
              ) : selected ? (
                <ContradictionCompare
                  contradiction={selected}
                  onResolve={handleResolve}
                  isResolving={resolve.isPending}
                />
              ) : (
                <EmptyState
                  title="좌측에서 모순을 선택하세요"
                  description="미해결 항목이 우선 정렬됩니다."
                />
              )}
            </main>
          </div>
        )}
      </div>
    </AppShell>
  );
}

const STATUS_KIND: Record<Contradiction["status"], StatusKind> = {
  pending: "warning",
  resolved: "success",
};

const STATUS_LABEL: Record<Contradiction["status"], string> = {
  pending: "미해결",
  resolved: "해결됨",
};

function ContradictionList({
  items,
  selectedId,
  onSelect,
}: {
  items: Contradiction[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <ul className="flex flex-col gap-2">
      {items.map((c) => {
        const selected = c.id === selectedId;
        return (
          <li key={c.id}>
            <button
              type="button"
              onClick={() => onSelect(c.id)}
              className={cn(
                "flex w-full flex-col gap-1.5 rounded-lg border p-3 text-left transition-colors",
                selected ? "border-accent bg-bg-info" : "border-border bg-bg hover:bg-bg-secondary",
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="line-clamp-1 text-sm font-medium text-fg">{c.subject}</span>
                <StatusDot kind={STATUS_KIND[c.status]} />
              </div>
              <p className="text-xs text-fg-tertiary">
                <span className="font-mono">{c.value_a}</span>
                <span className="mx-1">vs</span>
                <span className="font-mono">{c.value_b}</span>
              </p>
              <p className="font-mono text-[11px] text-fg-tertiary">
                {STATUS_LABEL[c.status]} · {c.created_at.slice(0, 10)}
              </p>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function AllResolvedView({ total, onNext }: { total: number; onNext: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 py-12 text-center">
      <CheckCircle2 className="h-12 w-12 text-fg-success" aria-hidden />
      <div className="flex flex-col gap-1">
        <h2 className="text-xl font-semibold text-fg">모든 모순 해결 완료</h2>
        <p className="text-sm text-fg-secondary">
          {total}건의 자료 모순을 모두 결정했습니다. 이제 인덱싱이 가능합니다.
        </p>
      </div>
      <Button size="lg" onClick={onNext}>
        진행 패널로 <ArrowRight className="ml-1 h-4 w-4" />
      </Button>
    </div>
  );
}
