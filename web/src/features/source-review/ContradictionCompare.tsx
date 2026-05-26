import { CheckCircle2 } from "lucide-react";
import type { Contradiction, ContradictionSource, ContradictionWinner } from "@/api/types";
import { SourceCard } from "@/components/data-display/SourceCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface ContradictionCompareProps {
  contradiction: Contradiction;
  onResolve: (winner: ContradictionWinner) => void;
  isResolving?: boolean;
}

const WINNER_LABEL: Record<ContradictionWinner, string> = {
  A: "자료 A 채택",
  B: "자료 B 채택",
  defer: "둘 다 보류",
};

export function ContradictionCompare({
  contradiction,
  onResolve,
  isResolving,
}: ContradictionCompareProps) {
  const resolved = contradiction.status === "resolved";
  const winner = contradiction.resolution?.winner;

  return (
    <div className="flex flex-col gap-5">
      <header className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <Badge variant="secondary" className="font-mono">
            검토 지점 #2
          </Badge>
          {resolved ? (
            <Badge variant="default" className="bg-bg-success text-fg-success">
              해결됨 — {winner ? WINNER_LABEL[winner] : "—"}
            </Badge>
          ) : null}
        </div>
        <h2 className="text-lg font-semibold text-fg">{contradiction.subject}</h2>
        <p className="text-sm text-fg-secondary">
          <span className="font-mono">자료 A: {contradiction.value_a}</span>
          <span className="mx-2 text-fg-tertiary">vs</span>
          <span className="font-mono">자료 B: {contradiction.value_b}</span>
        </p>
      </header>

      <div className="grid grid-cols-1 items-stretch gap-4 md:grid-cols-[1fr_auto_1fr]">
        <Side
          label="A"
          value={contradiction.value_a}
          source={contradiction.source_a}
          chosen={resolved && winner === "A"}
          dimmed={resolved && winner === "B"}
        />
        <div className="hidden items-center justify-center md:flex">
          <span className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-border-strong bg-bg-secondary font-mono text-xs font-medium text-fg-secondary">
            VS
          </span>
        </div>
        <Side
          label="B"
          value={contradiction.value_b}
          source={contradiction.source_b}
          chosen={resolved && winner === "B"}
          dimmed={resolved && winner === "A"}
        />
      </div>

      <footer className="flex flex-col gap-3 border-t border-border pt-4">
        {resolved ? (
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2 text-sm text-fg-success">
              <CheckCircle2 className="h-4 w-4" aria-hidden />
              <span>
                {winner ? WINNER_LABEL[winner] : "—"} ·{" "}
                {contradiction.resolution?.resolved_at?.slice(0, 10)}
              </span>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={isResolving}
                onClick={() => onResolve("A")}
              >
                자료 A로 재결정
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={isResolving}
                onClick={() => onResolve("B")}
              >
                자료 B로 재결정
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={isResolving}
                onClick={() => onResolve("defer")}
              >
                보류로 변경
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button
              size="lg"
              disabled={isResolving}
              onClick={() => onResolve("A")}
              className="border-fg-success/40 bg-bg-success text-fg-success hover:bg-bg-success/80"
            >
              자료 A 채택
            </Button>
            <Button
              size="lg"
              disabled={isResolving}
              onClick={() => onResolve("B")}
              className="border-fg-success/40 bg-bg-success text-fg-success hover:bg-bg-success/80"
            >
              자료 B 채택
            </Button>
            <Button
              size="lg"
              variant="ghost"
              disabled={isResolving}
              onClick={() => onResolve("defer")}
            >
              둘 다 보류 (작성 시 결정)
            </Button>
          </div>
        )}
      </footer>
    </div>
  );
}

function Side({
  label,
  value,
  source,
  chosen,
  dimmed,
}: {
  label: "A" | "B";
  value: string;
  source: ContradictionSource;
  chosen: boolean;
  dimmed: boolean;
}) {
  return (
    <div
      className={cn(
        "flex flex-col gap-3 rounded-lg border bg-bg p-3 transition-opacity",
        chosen ? "border-fg-success/40 bg-bg-success" : "border-border",
        dimmed && "opacity-50",
      )}
    >
      <div className="flex items-center justify-between">
        <Badge variant="secondary" className="font-mono">
          자료 {label}
        </Badge>
        <span className="font-mono text-lg font-semibold text-fg">{value}</span>
      </div>
      <SourceCard
        title={source.title}
        source={`${source.source} · ${source.organization}`}
        publishedAt={source.published_at}
        pages={source.page_number}
        reliability={source.reliability}
      />
      <blockquote className="rounded border-l-2 border-fg-warning bg-bg-warning/60 px-3 py-2 text-sm italic text-fg">
        “{source.quote}”
      </blockquote>
    </div>
  );
}
