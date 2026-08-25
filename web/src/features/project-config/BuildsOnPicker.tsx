import { Link2, Plus, X } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { type FormIssue, formatRefLabel, LIMITS, parseRefLabel } from "./validation";

// ─── 앞 절 이어받기 고르기 ───
// 종전에는 자유 입력 한 칸이었다("6.2(총사업비), 2.*"). 표기를 외워야 했고, 없는
// 절을 적어도 만들기 직전까지 몰랐으며, 절을 끼워 넣으면 번호가 밀려 조용히 다른
// 절을 가리켰다(2026-08-25 사용자 지적: "자유도가 너무 높아 틀리기 쉽다").
//
// 지금은 **목차에서 고른다** - 고를 수 있는 것만 목록에 있으므로 유령 절·자기 참조·
// 표기 오류가 애초에 만들어지지 않는다. 뒤 절은 목록에 없다: 이어받기는 앞 절이
// **확정한 값**을 받는 계약이라 아직 쓰이지 않은 절에서 받을 값이 없다.
//
// 저장 값은 종전과 같은 문자열("4.1"|"4.1(총사업비)"|"4.*")이다 - 서버·프리셋
// 계약을 그대로 두고 입력 방법만 바꾼다(옛 프로젝트·프리셋도 그대로 열린다).

export interface BuildsOnChapter {
  title: string;
  sections: { title: string }[];
}

export interface BuildsOnPickerProps {
  value: string[];
  onChange: (next: string[]) => void;
  /** 이 목차 전체 - 고를 수 있는 후보의 출처 */
  chapters: BuildsOnChapter[];
  /** 이 절의 위치(0-base) - 자기 자신과 뒤 절을 목록에서 뺀다 */
  selfChapter: number;
  selfSection: number;
  issue?: FormIssue;
}

interface Option {
  /** 저장될 표기 - "4.1" | "4.*" */
  label: string;
  /** 사람이 읽는 이름 - 절 제목 또는 "장 전체" */
  name: string;
  isWhole: boolean;
}

/** 앞 절 후보 - 앞 장의 모든 절 + 이 장의 앞 절, 그리고 장 전체(이 장 포함).
 * 이 장 전체(예: "4.*")는 X.5(시사점)류가 그 장의 사실 대장을 통째로 받는 정석 용례다. */
function candidates(
  chapters: BuildsOnChapter[],
  selfChapter: number,
  selfSection: number,
): { chapterLabel: string; options: Option[] }[] {
  const groups: { chapterLabel: string; options: Option[] }[] = [];
  chapters.forEach((ch, ci) => {
    if (ci > selfChapter) return; // 뒤 장은 아직 쓰이지 않았다
    const titled = ch.sections
      .map((s, si) => ({ s, si }))
      .filter(({ s }) => s.title.trim().length > 0);
    if (titled.length === 0) return;
    const options: Option[] = [];
    // 이 장 전체("4.*")는 앞에 확정된 절이 있어야 뜻이 있다 - 장의 첫 절이 자기 장
    // 전체를 받으면 아직 쓰이지 않은 뒤 절을 가리키게 된다.
    if (ci < selfChapter || selfSection > 0) {
      options.push({
        label: `${ci + 1}.*`,
        name:
          ci === selfChapter
            ? `${ci + 1}장 전체 (이 장에서 앞서 확정된 값)`
            : `${ci + 1}장 전체 (확정된 값을 모두 받음)`,
        isWhole: true,
      });
    }
    for (const { s, si } of titled) {
      if (ci === selfChapter && si >= selfSection) continue; // 자기 자신·뒤 절 제외
      options.push({ label: `${ci + 1}.${si + 1}`, name: s.title.trim(), isWhole: false });
    }
    if (options.length > 0) {
      groups.push({
        chapterLabel: `${ci + 1}장 ${ch.title.trim() || "(제목 없음)"}`,
        options,
      });
    }
  });
  return groups;
}

/** 저장된 표기 → 화면에 보일 이름. 대상이 사라졌으면 null(빨갛게 알린다). */
function describe(raw: string, chapters: BuildsOnChapter[]): { name: string } | null {
  const ref = parseRefLabel(raw);
  if (!ref) return null;
  const ch = chapters[ref.chapter - 1];
  if (!ch) return null;
  if (ref.section === null) return { name: `${ref.chapter}장 전체` };
  const title = ch.sections[ref.section - 1]?.title.trim();
  if (!title) return null;
  return { name: title };
}

export function BuildsOnPicker({
  value,
  onChange,
  chapters,
  selfChapter,
  selfSection,
  issue,
}: BuildsOnPickerProps) {
  const [picking, setPicking] = useState(false);
  const groups = candidates(chapters, selfChapter, selfSection);
  const chosen = new Set(
    value.map((v) => parseRefLabel(v)).map((r) => (r ? `${r.chapter}.${r.section ?? "*"}` : "")),
  );
  const full = value.length >= LIMITS.refsPerSection;
  const noCandidates = groups.length === 0;

  const setMetric = (index: number, metric: string) => {
    const ref = parseRefLabel(value[index]);
    if (!ref) return;
    onChange(value.map((v, i) => (i === index ? formatRefLabel({ ...ref, metric }) : v)));
  };

  return (
    <div className="flex flex-col gap-1.5">
      {value.map((raw, index) => {
        const ref = parseRefLabel(raw);
        const found = describe(raw, chapters);
        const broken = !ref || !found;
        // key는 **대상**으로 잡는다 - raw로 잡으면 지표를 한 글자 칠 때마다 문자열이
        // 바뀌어 행이 통째로 다시 그려지고 입력 포커스가 빠진다.
        const rowKey = ref ? `${ref.chapter}.${ref.section ?? "*"}` : raw;
        return (
          <div
            key={rowKey}
            className={cn(
              "flex flex-wrap items-center gap-2 rounded border px-2 py-1.5",
              broken ? "border-fg-danger bg-bg-danger" : "border-border bg-bg",
            )}
          >
            <span
              className={cn(
                "shrink-0 rounded-sm px-1.5 py-0.5 font-mono text-xs",
                broken ? "bg-bg-danger text-fg-danger" : "bg-bg-tertiary text-fg-secondary",
              )}
            >
              {ref ? `${ref.chapter}.${ref.section ?? "*"}` : raw}
            </span>
            <span
              className={cn(
                "min-w-0 flex-1 truncate text-xs",
                broken ? "text-fg-danger" : "text-fg",
              )}
            >
              {found ? found.name : "이 목차에 없는 절입니다. 지우고 다시 고르세요"}
            </span>
            {ref && ref.section !== null && !broken ? (
              // 지표 지정은 선택 - 적으면 그 절의 그 값만 받아 쓴다(사람·프리셋 전용 표기).
              <Input
                value={ref.metric ?? ""}
                placeholder="지표(선택) 예: 총사업비"
                onChange={(e) => setMetric(index, e.target.value)}
                className="h-7 w-40 bg-bg text-xs"
              />
            ) : null}
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 w-7 shrink-0 p-0 text-fg-tertiary hover:text-fg-danger"
              aria-label={`이어받기 ${raw} 지우기`}
              onClick={() => onChange(value.filter((_, i) => i !== index))}
            >
              <X className="h-3.5 w-3.5" aria-hidden />
            </Button>
          </div>
        );
      })}

      {picking && !full && !noCandidates ? (
        <div className="flex flex-col gap-2 rounded border border-accent/40 bg-bg-info p-2">
          <p className="text-[11px] text-fg-secondary">
            이 절보다 앞에 있는 절만 고를 수 있습니다. 아직 쓰이지 않은 절에는 받을 확정 값이
            없습니다.
          </p>
          {groups.map((group) => (
            <div key={group.chapterLabel} className="flex flex-col gap-1">
              <span className="text-[11px] font-medium text-fg-tertiary">{group.chapterLabel}</span>
              <div className="flex flex-wrap gap-1.5">
                {group.options.map((opt) => {
                  const already = chosen.has(opt.label.replace(/\(.*\)$/, ""));
                  return (
                    <button
                      key={opt.label}
                      type="button"
                      disabled={already}
                      onClick={() => {
                        onChange([...value, opt.label]);
                        setPicking(false);
                      }}
                      className={cn(
                        "flex max-w-full items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs transition-colors",
                        already
                          ? "cursor-not-allowed border-border bg-bg-secondary text-fg-tertiary"
                          : "border-border bg-bg text-fg-secondary hover:border-accent hover:text-fg",
                      )}
                    >
                      <span className="font-mono text-[11px]">{opt.label}</span>
                      <span className="truncate">{opt.name}</span>
                      {already ? <span className="text-[10px]">(이미 선택)</span> : null}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="w-fit text-fg-secondary"
            onClick={() => setPicking(false)}
          >
            닫기
          </Button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className={cn("w-fit", issue?.level === "blocker" && "border-fg-danger")}
            disabled={full || noCandidates}
            onClick={() => setPicking(true)}
          >
            <Link2 className="mr-1 h-3.5 w-3.5" aria-hidden />
            {value.length === 0 ? "앞 절 고르기" : "하나 더 고르기"}
            <Plus className="ml-1 h-3.5 w-3.5" aria-hidden />
          </Button>
          {noCandidates ? (
            <span className="text-[11px] text-fg-tertiary">
              첫 절이라 앞에 이어받을 절이 없습니다.
            </span>
          ) : full ? (
            <span className="text-[11px] text-fg-tertiary">
              절당 {LIMITS.refsPerSection}개까지 고를 수 있습니다. 더 필요하면 장 전체를 고르세요.
            </span>
          ) : null}
        </div>
      )}
    </div>
  );
}
