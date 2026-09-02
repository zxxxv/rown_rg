import { FilePlus2, Search, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { usePresets } from "@/api/presets";
import { useProjectListInfinite } from "@/api/projects";
import { type ProjectSort, ProjectSortSchema } from "@/api/types";
import { ProjectCard } from "@/components/data-display/ProjectCard";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth } from "@/hooks/useAuth";
import { useDebounce } from "@/hooks/useDebounce";
import { cn } from "@/lib/utils";

// 상태 탭 - 단일 단계 대신 '진행 중'(미완료 단계 묶음)·'완료'로 단순화.
// 'in_progress'는 백엔드 그룹 필터 토큰(created~reviewing), 'completed'는 실제 단계값.
type StatusFilter = "all" | "in_progress" | "completed";
const STATUS_TABS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "전체" },
  { value: "in_progress", label: "진행 중" },
  { value: "completed", label: "완료" },
];

const SORT_OPTIONS: { value: ProjectSort; label: string }[] = [
  { value: "created_desc", label: "최신순" },
  { value: "title_asc", label: "제목순" },
];

const DEFAULT_SORT: ProjectSort = "created_desc";

function parseStatus(raw: string | null): StatusFilter {
  if (raw === "in_progress" || raw === "completed") return raw;
  return "all";
}

function parseSort(raw: string | null): ProjectSort {
  if (!raw) return DEFAULT_SORT;
  const result = ProjectSortSchema.safeParse(raw);
  return result.success ? result.data : DEFAULT_SORT;
}

export default function ProjectsPage() {
  const { user, logout } = useAuth();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();

  const status = parseStatus(params.get("status"));
  // 보고서 유형 필터 - 서버 파라미터라 내 프로젝트·전체 프로젝트 모두에 같은 방식으로 걸린다
  const preset = params.get("preset") ?? "";
  const presetsQuery = usePresets();
  const sort = parseSort(params.get("sort"));
  const qRaw = params.get("q") ?? "";
  // 관리자만 전체 프로젝트 조회 가능(scope=all). 일반 사용자는 항상 자기 것.
  const isAdmin = user?.role === "admin" || user?.role === "super_admin";
  // 뷰어는 열람 전용 - 생성 진입점을 숨기지 않고 막힌 이유를 보여준다(2026-08-14 결정).
  const readOnly = user?.role === "viewer";
  const scope: "mine" | "all" = isAdmin && params.get("scope") === "all" ? "all" : "mine";

  const [searchInput, setSearchInput] = useState(qRaw);
  useEffect(() => {
    setSearchInput(qRaw);
  }, [qRaw]);
  const debouncedSearch = useDebounce(searchInput, 300);

  useEffect(() => {
    const normalized = debouncedSearch.trim();
    if (normalized === (params.get("q") ?? "")) return;
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (normalized) next.set("q", normalized);
        else next.delete("q");
        return next;
      },
      { replace: true },
    );
  }, [debouncedSearch, params, setParams]);

  // 상태·검색은 서버 파라미터(status·q) - 필터가 바뀌면 쿼리 키가 바뀌어 페이지가
  // 리셋된다. 정렬은 백엔드가 최신순 고정이므로 제목순만 클라이언트에서 적용.
  const filters = useMemo(
    () => ({
      ...(status !== "all" ? { status } : {}),
      ...(debouncedSearch.trim() ? { q: debouncedSearch.trim() } : {}),
      ...(preset ? { preset } : {}),
      ...(scope === "all" ? { scope: "all" as const } : {}),
    }),
    [status, debouncedSearch, preset, scope],
  );
  const { data, isLoading, isError, refetch, hasNextPage, fetchNextPage, isFetchingNextPage } =
    useProjectListInfinite(filters);

  const loadedItems = useMemo(() => (data?.pages ?? []).flat(), [data]);
  const items = useMemo(
    () =>
      sort === "title_asc"
        ? [...loadedItems].sort((a, b) => a.title.localeCompare(b.title, "ko"))
        : loadedItems,
    [loadedItems, sort],
  );

  const updateParam = (key: string, value: string | null) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (value) next.set(key, value);
      else next.delete(key);
      return next;
    });
  };

  const onStatusChange = (val: string) => {
    updateParam("status", val === "all" ? null : val);
  };
  const onSortChange = (val: string) => {
    updateParam("sort", val === DEFAULT_SORT ? null : val);
  };

  const hasActiveFilter = status !== "all" || Boolean(debouncedSearch.trim());

  return (
    <AppShell
      user={user ? { name: user.name, role: user.role } : null}
      onLogout={() => void logout()}
    >
      <div className="flex flex-col gap-6">
        {/* flex-wrap + nowrap 제목: 좁은 폭에서 제목이 짓눌리면 한글이 한 글자씩
            세로로 꺾인다(2026-08-20 폰 실측) - 오른쪽 묶음이 아랫줄로 내려가게 한다. */}
        <header className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="whitespace-nowrap text-3xl font-semibold text-fg">
              {scope === "all" ? "전체 프로젝트" : "내 프로젝트"}
            </h1>
            <p className="mt-1 text-sm text-fg-secondary">
              {items.length}건{hasNextPage ? "+" : ""}
              {hasActiveFilter ? " (필터 적용 중)" : ""}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {isAdmin ? (
              <Tabs
                value={scope}
                onValueChange={(v) => updateParam("scope", v === "all" ? "all" : null)}
              >
                <TabsList>
                  <TabsTrigger value="mine">내 프로젝트</TabsTrigger>
                  <TabsTrigger value="all">전체 프로젝트</TabsTrigger>
                </TabsList>
              </Tabs>
            ) : null}
            {readOnly ? (
              <div className="flex flex-col items-end gap-0.5">
                <Button disabled>
                  <FilePlus2 className="mr-1 h-4 w-4" />새 프로젝트
                </Button>
                <span className="text-xs text-fg-tertiary">
                  열람 전용 권한 - 생성은 관리자에게 문의하세요
                </span>
              </div>
            ) : (
              <Button onClick={() => navigate("/projects/new")}>
                <FilePlus2 className="mr-1 h-4 w-4" />새 프로젝트
              </Button>
            )}
          </div>
        </header>

        <div className="flex flex-col gap-3 rounded border border-border bg-bg p-4 lg:flex-row lg:items-center lg:justify-between">
          <Tabs value={status} onValueChange={onStatusChange}>
            <TabsList>
              {STATUS_TABS.map((t) => (
                <TabsTrigger key={t.value} value={t.value}>
                  {t.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>

          <div className="flex flex-wrap items-center gap-2">
            <Select
              value={preset || "__all__"}
              onValueChange={(v) =>
                setParams(
                  (prev) => {
                    const next = new URLSearchParams(prev);
                    if (v === "__all__") next.delete("preset");
                    else next.set("preset", v);
                    return next;
                  },
                  { replace: true },
                )
              }
            >
              <SelectTrigger className="w-40" aria-label="보고서 유형 필터">
                <SelectValue placeholder="유형 전체" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__all__">유형 전체</SelectItem>
                {/* 개인 프리셋(u:...)은 유형이 아니라 개인이 저장한 목차 - 유형 필터를
                    오염시키지 않게 카탈로그만 나열하고, 분류는 자유 주제에 포함시킨다
                    (2026-09-02 결정). 저장할 때마다 (2)(3)이 늘어 목록이 자라던 문제. */}
                {(presetsQuery.data ?? [])
                  .filter((p) => !p.id.startsWith("u:"))
                  .map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                <SelectItem value="blank">자유 주제</SelectItem>
              </SelectContent>
            </Select>

            <div className="relative w-full sm:w-auto">
              <Search
                className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-tertiary"
                aria-hidden
              />
              <Input
                type="search"
                placeholder={scope === "all" ? "제목·주제·소유자명 검색" : "제목·주제 검색"}
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                aria-label="프로젝트 검색"
                className={cn("w-full pl-8 sm:w-64", searchInput && "pr-8")}
              />
              {searchInput ? (
                <button
                  type="button"
                  aria-label="검색어 지우기"
                  onClick={() => setSearchInput("")}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-fg-tertiary hover:text-fg"
                >
                  <X className="h-4 w-4" />
                </button>
              ) : null}
            </div>

            <Select value={sort} onValueChange={onSortChange}>
              <SelectTrigger className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SORT_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        {isLoading ? (
          <LoadingSkeleton variant="card" count={6} />
        ) : isError ? (
          <EmptyState
            title="프로젝트를 불러오지 못했습니다"
            description="잠시 후 다시 시도해 주세요."
            action={
              <Button variant="outline" onClick={() => void refetch()}>
                다시 시도
              </Button>
            }
          />
        ) : items.length === 0 ? (
          hasActiveFilter ? (
            <EmptyState
              title="조건에 맞는 프로젝트가 없습니다"
              description="필터·검색어를 변경해 보세요."
              action={
                <Button
                  variant="outline"
                  onClick={() => {
                    setSearchInput("");
                    setParams({}, { replace: true });
                  }}
                >
                  필터 초기화
                </Button>
              }
            />
          ) : (
            <EmptyState
              icon={FilePlus2}
              title={readOnly ? "아직 열람할 프로젝트가 없습니다" : "첫 보고서를 만들어보세요"}
              description={
                readOnly
                  ? "지금 계정은 열람 전용입니다 - 프로젝트 생성 권한이 필요하면 관리자에게 문의하세요."
                  : "주제어만 입력하면 회사 양식의 보고서 초안이 자동으로 생성됩니다."
              }
              action={
                readOnly ? undefined : (
                  <Button onClick={() => navigate("/projects/new")}>새 프로젝트</Button>
                )
              }
            />
          )
        ) : (
          <>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {items.map((p) => (
                <ProjectCard
                  key={p.id}
                  project={p}
                  onClick={() => navigate(`/projects/${p.id}/overview`)}
                />
              ))}
            </div>
            {hasNextPage ? (
              <div className="flex justify-center">
                <Button
                  variant="outline"
                  onClick={() => void fetchNextPage()}
                  disabled={isFetchingNextPage}
                >
                  {isFetchingNextPage ? "불러오는 중…" : "더 보기"}
                </Button>
              </div>
            ) : null}
          </>
        )}
      </div>
    </AppShell>
  );
}
