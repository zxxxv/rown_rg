import { Plus } from "lucide-react";
import type { KeyboardEvent } from "react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { EDITOR_SAMPLE } from "@/api/mock/fixtures/editor-sample";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useRewriteDialog } from "@/features/editor/useRewriteDialog";
import { cn } from "@/lib/utils";

const MAX_REASON = 500;

interface ChipDef {
  id: string;
  label: string;
  suffix: string;
}

const CHIPS: ChipDef[] = [
  { id: "specific", label: "더 구체적으로", suffix: "\n[지시] 더 구체적인 수치와 인용을 포함" },
  { id: "shorter", label: "더 짧게", suffix: "\n[지시] 핵심만 간결하게" },
  { id: "formal", label: "더 격식 있게", suffix: "\n[지시] 공식 보고서 톤으로" },
  { id: "counter", label: "반론도 포함", suffix: "\n[지시] 반론 관점을 함께 다룰 것" },
];

type Tone = "current" | "formal" | "plain";

const TONE_OPTIONS: { value: Tone; label: string }[] = [
  { value: "current", label: "현재 톤 유지" },
  { value: "formal", label: "격식 강화" },
  { value: "plain", label: "평이하게" },
];

export function RewriteDialog() {
  const { open, componentId, closeRewrite } = useRewriteDialog();
  const [reason, setReason] = useState("");
  const [keepSources, setKeepSources] = useState(true);
  const [includeOther, setIncludeOther] = useState(false);
  const [tone, setTone] = useState<Tone>("current");

  // Reset state when dialog opens with a new component
  useEffect(() => {
    if (open) {
      setReason("");
      setKeepSources(true);
      setIncludeOther(false);
      setTone("current");
    }
  }, [open]);

  const component = componentId ? EDITOR_SAMPLE.components.find((c) => c.id === componentId) : null;

  const activeChips = CHIPS.filter((c) => reason.includes(c.suffix));
  const overflow = reason.length > MAX_REASON;
  const canSubmit = (reason.trim().length > 0 || activeChips.length > 0) && !overflow;

  const toggleChip = (chip: ChipDef) => {
    const isActive = reason.includes(chip.suffix);
    if (isActive) {
      setReason((prev) => prev.replace(chip.suffix, ""));
    } else {
      setReason((prev) => prev + chip.suffix);
    }
  };

  const submit = () => {
    if (!canSubmit) return;
    closeRewrite();
    toast.success("재작성 시작됨", {
      description:
        "진행 패널과 통합되어 작성 모델에 전송되고, 완료 후 비교 뷰가 표시됩니다. (현재는 모킹 단계)",
      duration: 5000,
    });
  };

  const onTextareaKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && closeRewrite()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>컴포넌트 재작성</DialogTitle>
          <DialogDescription>
            {component ? (
              <span className="font-mono text-xs">
                {component.id} · {component.type}
              </span>
            ) : (
              "선택된 컴포넌트가 없습니다."
            )}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-5">
          <section className="flex flex-col gap-2">
            <Label htmlFor="rewrite-reason" className="text-sm font-medium text-fg">
              왜 다시 쓰고 싶은가요?
            </Label>
            <Textarea
              id="rewrite-reason"
              rows={4}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              onKeyDown={onTextareaKeyDown}
              placeholder="예: 비용 산출 근거가 부족합니다. 2024년 자료 우선으로 다시 써주세요."
            />
            <div className="flex items-center justify-between text-xs">
              <span className="text-fg-tertiary">Cmd/Ctrl + Enter로 빠른 시작</span>
              <span className={cn("font-mono", overflow ? "text-fg-danger" : "text-fg-tertiary")}>
                {reason.length} / {MAX_REASON}
              </span>
            </div>
          </section>

          <section className="flex flex-col gap-2">
            <p className="text-xs font-medium uppercase tracking-wide text-fg-tertiary">
              빠른 선택
            </p>
            <div className="flex flex-wrap gap-2">
              {CHIPS.map((chip) => {
                const active = reason.includes(chip.suffix);
                return (
                  <button
                    key={chip.id}
                    type="button"
                    onClick={() => toggleChip(chip)}
                    aria-pressed={active}
                    className={cn(
                      "rounded-full border px-3 py-1 text-xs transition-colors",
                      active
                        ? "border-accent bg-bg-info text-fg"
                        : "border-border bg-bg text-fg-secondary hover:border-border-strong hover:bg-bg-secondary",
                    )}
                  >
                    {chip.label}
                  </button>
                );
              })}
            </div>
          </section>

          <section className="flex flex-col gap-2">
            <p className="text-xs font-medium uppercase tracking-wide text-fg-tertiary">
              참고 자료
            </p>
            <p className="text-xs text-fg-tertiary">
              현재 출처:{" "}
              {component?.src_ids.length ? (
                <span className="font-mono">{component.src_ids.join(", ")}</span>
              ) : (
                <span>없음</span>
              )}
            </p>
            <Button
              variant="outline"
              size="sm"
              className="w-fit"
              onClick={() =>
                toast("자료실 선택 다이얼로그 — 구현 예정", {
                  description: "자료 라이브러리·검색·업로드 자료에서 선택 가능합니다.",
                })
              }
            >
              <Plus className="mr-1 h-3.5 w-3.5" /> 추가 자료 선택
            </Button>
          </section>

          <Accordion type="single" collapsible>
            <AccordionItem value="adv" className="border-border">
              <AccordionTrigger className="text-sm hover:no-underline">고급 옵션</AccordionTrigger>
              <AccordionContent className="flex flex-col gap-3 pt-2">
                <SwitchRow
                  id="opt-keep-sources"
                  label="기존 출처 유지"
                  description="추가 자료만 더하고 기존 src_ids는 그대로."
                  checked={keepSources}
                  onChange={setKeepSources}
                />
                <SwitchRow
                  id="opt-include-other"
                  label="다른 섹션의 컴포넌트도 함께 확인"
                  description="같은 출처를 공유하는 cross_references를 함께 검토."
                  checked={includeOther}
                  onChange={setIncludeOther}
                />
                <div className="flex flex-col gap-1.5">
                  <Label htmlFor="opt-tone" className="text-xs text-fg-secondary">
                    톤
                  </Label>
                  <Select value={tone} onValueChange={(v) => setTone(v as Tone)}>
                    <SelectTrigger id="opt-tone" className="w-48">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {TONE_OPTIONS.map((o) => (
                        <SelectItem key={o.value} value={o.value}>
                          {o.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </AccordionContent>
            </AccordionItem>
          </Accordion>

          {activeChips.length > 0 ? (
            <p className="rounded border border-border bg-bg-secondary p-2 text-xs text-fg-secondary">
              적용된 지시:{" "}
              {activeChips.map((c) => (
                <Badge key={c.id} variant="secondary" className="mr-1">
                  {c.label}
                </Badge>
              ))}
            </p>
          ) : null}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => closeRewrite()}>
            취소
          </Button>
          <Button type="button" onClick={submit} disabled={!canSubmit}>
            재작성 시작
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function SwitchRow({
  id,
  label,
  description,
  checked,
  onChange,
}: {
  id: string;
  label: string;
  description: string;
  checked: boolean;
  onChange: (next: boolean) => void;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="flex flex-col gap-0.5">
        <Label htmlFor={id} className="cursor-pointer text-sm text-fg">
          {label}
        </Label>
        <span className="text-xs text-fg-tertiary">{description}</span>
      </div>
      <Switch id={id} checked={checked} onCheckedChange={onChange} />
    </div>
  );
}
