import { Check, Layers, Loader2, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { estimateLabel, useCostBasis } from "@/api/cost";
import {
  useAdoptVariant,
  useDiscardVariants,
  useSectionVariants,
  useStartVariants,
} from "@/api/variants";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const DEFAULT_N = 3;

/** 한 절을 여러 벌 뽑아 나란히 놓고 고른다.
 *
 * 재작성은 한 번에 하나만 준다. 마음에 안 들면 다시 눌러야 하고, 그러면 방금 것은
 * 사라진다 - 둘을 견줄 수가 없었다. 실제로 사람이 하는 일은 "이게 나은가 저게 나은가"다.
 *
 * 값이 안 개수에 곱해지므로 누르기 전에 예상 비용을 보여준다. 뽑기와 적용은 다른
 * 행동이다: 뽑아 놓기만 하고 본문은 고를 때까지 그대로다.
 */
/** 하단 바에 놓는 실행 버튼. 결과는 본문 열의 패널이 받는다.
 *
 *  둘을 한 덩이로 두었더니 하단 고정 바가 화면의 12%에서 **49%로 부풀어** 본문을
 *  덮었다(실측 1500x1100: 본문 가용 562px). 안을 고르는 일은 본문과 견주는 일인데
 *  본문을 가리면 고를 수가 없다 - 그래서 트리거만 바에 남긴다.
 */
export function SectionVariantsTrigger({
  projectId,
  sectionId,
  instruction,
  disabled,
}: {
  projectId: string;
  sectionId: string;
  /** 재작성 바에 적힌 지시를 그대로 쓴다 - 지시 칸이 둘이면 어느 쪽이 먹는지 모른다 */
  instruction: string;
  disabled?: boolean;
}) {
  const query = useSectionVariants(projectId, sectionId);
  const start = useStartVariants(projectId, sectionId);
  const costBasis = useCostBasis(projectId);
  const data = query.data;
  const running = data?.running ?? false;
  const variants = data?.variants ?? [];
  // 이미 뽑아 둔 게 있으면 버튼을 감춘다 - 패널이 화면에 떠 있고, 거기에 버리기가 있다.
  if (variants.length > 0 || running) return null;

  const onStart = () => {
    start.mutate(
      { n: DEFAULT_N, instruction },
      {
        onSuccess: () => {
          toast.success(`${DEFAULT_N}개 안을 뽑고 있습니다`, {
            description: "완성되는 대로 본문 위에 쌓입니다. 본문은 고를 때까지 그대로입니다.",
          });
          void query.refetch();
        },
        onError: (err: unknown) =>
          toast.error("안 뽑기 실패", {
            description: err instanceof ApiError ? err.message : undefined,
          }),
      },
    );
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button
        variant="outline"
        size="sm"
        disabled={disabled || start.isPending}
        onClick={onStart}
        title="같은 절을 서로 다른 안으로 3벌 뽑아 나란히 보고 고릅니다"
      >
        <Layers className="mr-1 h-3.5 w-3.5" />
        {DEFAULT_N}개 안 뽑기
      </Button>
      {/* 금액만 - 근거(절당·출처)는 옆의 재작성 문구가 한 번 말한다. */}
      {estimateLabel(costBasis.data, DEFAULT_N, { compact: true }) ? (
        <span className="text-[11px] text-fg-tertiary">
          {estimateLabel(costBasis.data, DEFAULT_N, { compact: true })}
        </span>
      ) : null}
    </div>
  );
}

/** 본문 열 맨 위에 놓는 결과 패널 - 스크롤을 함께 타므로 얼마든 길어져도 된다.
 *  바로 아래가 현재 본문이라 "이게 나은가 저게 나은가"를 그 자리에서 견준다. */
export function SectionVariantsPanel({
  projectId,
  sectionId,
}: {
  projectId: string;
  sectionId: string;
}) {
  const query = useSectionVariants(projectId, sectionId);
  const discard = useDiscardVariants(projectId, sectionId);
  const adopt = useAdoptVariant(projectId, sectionId);
  const [openId, setOpenId] = useState<string | null>(null);

  const data = query.data;
  const running = data?.running ?? false;
  const variants = data?.variants ?? [];
  const failures = Object.entries(data?.failures ?? {});

  // 처음 나타나는 순간 그 자리로 데려간다. 버튼은 하단 바에 있고 패널은 본문 위라,
  // 누른 자리에서는 화면 밖이다 - 안 데려가면 "아무 일도 안 일어났다"로 읽힌다
  // (실측: 하단으로 스크롤한 상태에서 누르면 패널이 시야 밖에 생겼다).
  const box = useRef<HTMLElement>(null);
  const wasEmpty = useRef(true);
  useEffect(() => {
    const has = variants.length > 0 || running;
    if (has && wasEmpty.current) box.current?.scrollIntoView({ block: "start" });
    wasEmpty.current = !has;
  }, [variants.length, running]);

  const onAdopt = (id: string) => {
    adopt.mutate(id, {
      onSuccess: () => {
        setOpenId(null);
        toast.success("이 안을 본문으로 삼았습니다", {
          description: "이전 본문은 버전 기록에 남아 있습니다.",
        });
      },
      onError: (err: unknown) =>
        toast.error("채택 실패", {
          description: err instanceof ApiError ? err.message : undefined,
        }),
    });
  };

  if (variants.length === 0 && !running) return null;

  return (
    <section
      ref={box}
      className="flex w-full scroll-mt-4 flex-col gap-2 rounded-lg border border-border bg-bg-secondary p-3"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-fg">
          <Layers className="h-4 w-4 shrink-0" aria-hidden />안 고르기 {variants.length}개
          {running ? (
            <span className="ml-1 inline-flex items-center gap-1 text-xs font-normal text-fg-secondary">
              <Loader2 className="h-3 w-3 animate-spin" />
              {data?.done ?? 0}/{data?.total ?? 0} 뽑는 중
            </span>
          ) : null}
        </h3>
        <Button
          variant="ghost"
          size="sm"
          className="h-7 px-2 text-xs"
          disabled={discard.isPending}
          onClick={() => discard.mutate()}
        >
          <Trash2 className="mr-1 h-3.5 w-3.5" />
          {running ? "멈추고 버리기" : "버리기"}
        </Button>
      </div>

      <p className="text-xs text-fg-secondary">
        본문은 고를 때까지 그대로입니다. 고른 안은 재작성과 같은 경로로 반영되고, 이전 본문은 버전
        기록에 남습니다.
      </p>

      <ul className="flex flex-col gap-2">
        {variants.map((v, i) => (
          <li key={v.id} className="rounded border border-border bg-bg p-2">
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="font-medium text-fg">{i + 1}안</span>
              <span className="text-fg-tertiary">
                {v.n_chars.toLocaleString()}자 · 인용 {v.n_markers} · 근거 {v.evidence_count}
              </span>
              {v.volume_scaled ? (
                <Badge variant="outline" className="font-normal text-fg-warning">
                  재료 부족으로 분량 낮춤
                </Badge>
              ) : null}
              <span className="ml-auto flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  onClick={() => setOpenId((cur) => (cur === v.id ? null : v.id))}
                >
                  {openId === v.id ? "접기" : "전문 보기"}
                </Button>
                <Button
                  size="sm"
                  className="h-7 px-2 text-xs"
                  disabled={adopt.isPending}
                  onClick={() => onAdopt(v.id)}
                >
                  <Check className="mr-1 h-3.5 w-3.5" />
                  이걸로
                </Button>
              </span>
            </div>
            <p
              className={cn(
                "mt-1 whitespace-pre-wrap text-xs leading-relaxed text-fg-secondary",
                openId === v.id ? "max-h-96 overflow-y-auto" : "line-clamp-3",
              )}
            >
              {v.content}
            </p>
          </li>
        ))}
      </ul>

      {failures.length > 0 ? (
        // 셋 중 둘만 나온 사실을 삼키지 않는다 - 값은 이미 나갔다.
        <ul className="flex flex-col gap-1 border-t border-border pt-2 text-xs text-fg-danger">
          {failures.map(([label, reason]) => (
            <li key={label}>
              {label} - {reason}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
