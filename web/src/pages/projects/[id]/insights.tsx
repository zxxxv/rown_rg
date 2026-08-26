import { ArrowLeft, Download, FileText, ListChecks, Loader2, RefreshCw } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { useInsights, useRebuildInsights } from "@/api/insights";
import { useProject } from "@/api/projects";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { env } from "@/env";
import { useDownload } from "@/features/export/useDownload";
import { MarkdownContent } from "@/features/preview/MarkdownContent";
import { useAuth } from "@/hooks/useAuth";

/** 시사점 요약 - 본문의 시사점·제언 절을 2~3쪽으로 압축한 별도 산출물.
 *
 * 원본 보고서는 건드리지 않는다. 이 요약은 본문 HWPX에 실리지 않고(2026-08-25 결정)
 * 요약만 담은 **별도 한글 파일**로 내려받는다(2026-08-27 결정) - 화면이 그 갈래를
 * 명시해야 사용자가 본문 파일에서 찾다가 없다고 오해하지 않는다.
 */
function fmtElapsed(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

function fmtDateTime(iso: string): string {
  return new Date(iso).toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export default function InsightsPage() {
  const { id: projectId = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const projectQuery = useProject(projectId);
  const insightsQuery = useInsights(projectId);
  const rebuild = useRebuildInsights(projectId);
  const { download, pending: downloading } = useDownload();

  const insights = insightsQuery.data;
  const running = insights?.running ?? false;
  const isCompleted = projectQuery.data?.status === "completed";
  const reportTitle = projectQuery.data?.title ?? "보고서";

  // 다시 만들기가 끝난 순간을 알린다. 요약 호출은 온도 0이라 본문이 그대로면 **같은
  // 요약**이 나온다 - 화면이 안 바뀌니 눌러도 아무 일이 없는 것처럼 보인다(2026-08-27
  // 지적). 끝났다는 사실과 내용이 같았다는 사실을 나눠서 말해 준다.
  // 눌린 순간부터 서버가 '끝'이라고 할 때까지가 도는 중이다. POST 왕복(isPending)과
  // 서버가 아는 running 사이에 한 순간 틈이 있어, 그때 버튼이 되살아나 두 번 눌리던
  // 자리다 - 둘을 OR로 묶고 첫 폴링이 올 때까지는 눌린 사실을 그대로 붙들어 둔다.
  const [justStarted, setJustStarted] = useState(false);
  const busy = running || rebuild.isPending || justStarted;
  useEffect(() => {
    if (running) setJustStarted(false);
  }, [running]);

  // 30초~2분이 걸린다 - 경과 시간이 없으면 멈춘 것과 도는 것이 화면에서 같아 보인다.
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!busy) {
      setElapsed(0);
      return;
    }
    const started = Date.now();
    const id = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(id);
  }, [busy]);

  const prev = useRef<{ running: boolean; content: string | null }>({
    running: false,
    content: null,
  });
  useEffect(() => {
    const before = prev.current;
    const content = insights?.content ?? null;
    if (before.running && !running) {
      toast.success(
        before.content && before.content === content
          ? "요약을 다시 만들었습니다 - 내용은 이전과 같습니다"
          : "시사점 요약을 새로 만들었습니다",
        {
          description:
            before.content && before.content === content
              ? "본문이 그대로면 같은 요약이 나옵니다."
              : undefined,
        },
      );
    }
    prev.current = { running, content };
  }, [running, insights?.content]);

  // 본문 다운로드와 같은 방식(fetch+blob) - 서버가 저장된 요약에서 그때그때 렌더한다.
  const onDownload = () =>
    void download({
      url: `${env.VITE_API_BASE_URL.replace(/\/$/, "")}/projects/${projectId}/insights/export`,
      filename: `${reportTitle} 시사점 요약.hwpx`,
      label: "시사점 요약 HWPX",
    });

  // 어떤 절을 근거로 삼을지는 사람이 고른다 - 자동 선택은 제목 규칙에 기대는
  // 추측이라 장별 시사점이 여러 개인 보고서에서 엉뚱한 절을 물어 왔다(2026-08-27).
  const [picking, setPicking] = useState(false);
  const [chosen, setChosen] = useState<Set<string> | null>(null);
  const selected = chosen ?? new Set(insights?.selected_section_ids ?? []);
  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    setChosen(next);
  };

  const onRebuild = (ids?: string[]) => {
    rebuild.mutate(ids, {
      onSuccess: () => {
        setJustStarted(true);
        toast.success("시사점 요약을 다시 만들고 있습니다", {
          description: "본문 분량에 따라 30초~2분쯤 걸립니다.",
        });
      },
      onError: () => toast.error("다시 만들기를 시작하지 못했습니다"),
    });
  };

  return (
    <AppShell
      user={user ? { name: user.name, role: user.role } : null}
      onLogout={() => void logout()}
    >
      <Button
        variant="ghost"
        size="sm"
        className="mb-3 w-fit text-fg-secondary"
        onClick={() => navigate(`/projects/${projectId}/overview`)}
      >
        <ArrowLeft className="mr-1 h-4 w-4" />
        프로젝트 개요
      </Button>

      {/* 읽는 문서라 폭을 묶는다 - 전폭(1440px)이면 한 줄이 너무 길어 눈이 줄을
          놓친다. 본문 카드와 머리글의 좌우가 같은 선에 오도록 같은 폭을 쓴다. */}
      <header className="mx-auto mb-4 flex w-full max-w-4xl flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-lg font-semibold text-fg">시사점 요약</h1>
            <Badge variant="outline">본문 미포함 · 별도 파일</Badge>
          </div>
          <p className="text-sm text-fg-secondary">
            본문의 시사점·제언 절을 2~3쪽으로 압축한 별도 산출물입니다. 원본 보고서는 그대로이며, 이
            요약은 본문 HWPX에 들어가지 않는 대신 요약만 담은 한글 파일로 따로 받을 수 있습니다.
          </p>
          {insights?.built_at ? (
            <p className="text-xs text-fg-secondary">
              마지막 생성 {fmtDateTime(insights.built_at)}
              {insights.model ? ` · ${insights.model}` : ""}
            </p>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            onClick={onDownload}
            disabled={!insights?.content || downloading}
            title={
              insights?.content
                ? "이 요약만 담은 한글 파일을 내려받습니다(본문 보고서와 별개 파일)"
                : "요약이 만들어진 뒤에 받을 수 있습니다"
            }
          >
            <Download className="mr-1 h-4 w-4" />
            {downloading ? "준비 중…" : "한글 파일 내려받기"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => onRebuild()}
            disabled={busy || !isCompleted}
            title={
              isCompleted
                ? "지금 저장된 본문으로 요약을 다시 만듭니다"
                : "본문이 완성된 뒤에 만들 수 있습니다"
            }
          >
            {busy ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" />
            ) : (
              <RefreshCw className="mr-1 h-4 w-4" />
            )}
            {busy ? `만드는 중… ${fmtElapsed(elapsed)}` : "다시 만들기"}
          </Button>
        </div>
      </header>

      {insightsQuery.isLoading ? (
        <LoadingSkeleton />
      ) : insightsQuery.isError ? (
        <EmptyState
          title="시사점 요약을 불러올 수 없습니다"
          description="잠시 후 다시 시도해 주세요."
          action={
            <Button variant="outline" onClick={() => void insightsQuery.refetch()}>
              다시 시도
            </Button>
          }
        />
      ) : insights?.content ? (
        <article className="mx-auto w-full max-w-4xl rounded border border-border bg-bg">
          {/* 무엇을 압축한 요약인지가 먼저다 - 본문 끝에 두면 다 읽고 나서야 출처를
              만난다(2026-08-27 지시로 맨 위로 올리고 글씨를 키움). */}
          {insights.source_sections.length > 0 ? (
            <header className="flex flex-wrap items-center gap-2 border-b border-border px-6 py-4 text-base text-fg-secondary">
              <FileText className="h-5 w-5 shrink-0" aria-hidden />
              <span className="shrink-0 font-medium text-fg">요약한 절</span>
              {insights.source_sections.map((label) => (
                <button
                  key={label}
                  type="button"
                  className="whitespace-nowrap rounded border border-border px-3 py-1.5 text-base text-fg hover:bg-bg-secondary"
                  onClick={() => navigate(`/projects/${projectId}/preview`)}
                >
                  {label}
                </button>
              ))}
              {/* 무엇을 요약할지는 사람이 정한다 - 여기가 그 문이다. */}
              <Button
                variant="outline"
                size="sm"
                className="ml-auto shrink-0"
                onClick={() => setPicking((v) => !v)}
                aria-expanded={picking}
              >
                <ListChecks className="mr-1 h-4 w-4" aria-hidden />
                {picking ? "고르기 닫기" : "절 고르기"}
              </Button>
            </header>
          ) : null}
          {picking ? (
            <div className="flex flex-col gap-2 border-b border-border bg-bg-secondary px-6 py-4">
              <p className="text-sm text-fg-secondary">
                요약의 근거로 삼을 절을 고르세요. 고른 절에 있는 사실·값만 쓰이고, 여기 없는 내용은
                요약에 들어가지 않습니다. 아무것도 안 고르면 제목이 시사점·제언·결론인 절을 자동으로
                씁니다.
              </p>
              <ul className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
                {insights.selectable.map((o) => (
                  <li key={o.section_id}>
                    <label className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-sm hover:bg-bg">
                      <input
                        type="checkbox"
                        className="h-4 w-4 shrink-0 accent-accent"
                        checked={selected.has(o.section_id)}
                        onChange={() => toggle(o.section_id)}
                      />
                      <span className="min-w-0 flex-1 truncate text-fg">{o.label}</span>
                      <span className="shrink-0 text-xs text-fg-tertiary">
                        {o.chars.toLocaleString()}자
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  disabled={busy}
                  onClick={() => {
                    setPicking(false);
                    onRebuild([...selected]);
                  }}
                >
                  <RefreshCw className="mr-1 h-4 w-4" aria-hidden />
                  고른 절로 다시 만들기
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  onClick={() => {
                    setChosen(new Set());
                    setPicking(false);
                    onRebuild([]);
                  }}
                  title="선택을 지우고 제목 규칙으로 자동 선택합니다"
                >
                  자동 선택으로
                </Button>
                <span className="text-xs text-fg-tertiary">
                  고른 절 {selected.size}개 ·{" "}
                  {insights.selectable
                    .filter((o) => selected.has(o.section_id))
                    .reduce((n, o) => n + o.chars, 0)
                    .toLocaleString()}
                  자
                </span>
              </div>
            </div>
          ) : null}
          <div className="px-6 py-4">
            <MarkdownContent content={insights.content} />
          </div>
        </article>
      ) : running ? (
        <EmptyState
          title="시사점 요약을 만들고 있습니다"
          description="본문 분량에 따라 30초~2분쯤 걸립니다. 다 되면 이 화면이 저절로 바뀝니다."
        />
      ) : (
        <EmptyState
          title="아직 시사점 요약이 없습니다"
          description={
            isCompleted
              ? "'다시 만들기'를 누르면 지금 저장된 본문으로 요약을 만듭니다."
              : "본문이 완성되면 조립 단계에서 자동으로 만들어집니다."
          }
          action={
            isCompleted ? (
              <Button onClick={() => onRebuild()} disabled={busy}>
                {busy ? (
                  <Loader2 className="mr-1 h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="mr-1 h-4 w-4" />
                )}
                {busy ? `만드는 중… ${fmtElapsed(elapsed)}` : "지금 만들기"}
              </Button>
            ) : undefined
          }
        />
      )}
    </AppShell>
  );
}
