import { FilePlus2, Search, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useProjectListInfinite } from "@/api/projects";
import {
  type ProjectSort,
  ProjectSortSchema,
  type ProjectStatus,
  ProjectStatusSchema,
} from "@/api/types";
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

const STATUS_TABS: { value: "all" | ProjectStatus; label: string }[] = [
  { value: "all", label: "전체" },
  { value: "writing", label: "작성 중" },
  { value: "reviewing", label: "검토 대기" },
  { value: "completed", label: "완료" },
  { value: "archived", label: "보관" },
];

const SORT_OPTIONS: { value: ProjectSort; label: string }[] = [
  { value: "created_desc", label: "최신순" },
  { value: "title_asc", label: "제목순" },
];

const DEFAULT_SORT: ProjectSort = "created_desc";

function parseStatus(raw: string | null): ProjectStatus | "all" {
  if (!raw || raw === "all") return "all";
  const result = ProjectStatusSchema.safeParse(raw);
  return result.success ? result.data : "all";
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
  const sort = parseSort(params.get("sort"));
  const qRaw = params.get("q") ?? "";

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

  // 실백엔드 목록은 limit/offset만 지원(최신순 고정) — 상태·검색·제목순은 로드된
  // 페이지에 대해 클라이언트에서 적용한다.
  const { data, isLoading, isError, refetch, hasNextPage, fetchNextPage, isFetchingNextPage } =
    useProjectListInfinite();

  const loadedItems = useMemo(() => (data?.pages ?? []).flat(), [data]);
  const items = useMemo(() => {
    let result = loadedItems;
    if (status !== "all") result = result.filter((p) => p.status === status);
    const q = debouncedSearch.trim().toLowerCase();
    if (q) {
      result = result.filter(
        (p) => p.title.toLowerCase().includes(q) || p.topic.toLowerCase().includes(q),
      );
    }
    if (sort === "title_asc") {
      result = [...result].sort((a, b) => a.title.localeCompare(b.title, "ko"));
    }
    return result;
  }, [loadedItems, status, debouncedSearch, sort]);

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
      tokenUsage={{ used: 1_240_000, limit: 5_000_000 }}
    >
      <div className="flex flex-col gap-6">
        <header className="flex items-center justify-between gap-4">
          <div>
            <h1 className="text-3xl font-semibold text-fg">내 프로젝트</h1>
            <p className="mt-1 text-sm text-fg-secondary">
              {items.length}건{hasNextPage ? "+" : ""}
              {hasActiveFilter ? " (필터 적용 중)" : ""}
            </p>
          </div>
          <Button onClick={() => navigate("/projects/new")}>
            <FilePlus2 className="mr-1 h-4 w-4" />새 프로젝트
          </Button>
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

          <div className="flex items-center gap-2">
            <div className="relative">
              <Search
                className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-tertiary"
                aria-hidden
              />
              <Input
                type="search"
                placeholder="제목·주제 검색"
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                aria-label="프로젝트 검색"
                className={cn("w-64 pl-8", searchInput && "pr-8")}
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
              description="필터·검색어를 변경하거나, 아직 불러오지 않은 페이지를 더 불러와 보세요."
              action={
                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    onClick={() => {
                      setSearchInput("");
                      setParams({}, { replace: true });
                    }}
                  >
                    필터 초기화
                  </Button>
                  {hasNextPage ? (
                    <Button
                      variant="outline"
                      onClick={() => void fetchNextPage()}
                      disabled={isFetchingNextPage}
                    >
                      {isFetchingNextPage ? "불러오는 중…" : "더 불러오기"}
                    </Button>
                  ) : null}
                </div>
              }
            />
          ) : (
            <EmptyState
              icon={FilePlus2}
              title="첫 보고서를 만들어보세요"
              description="주제어만 입력하면 회사 양식의 보고서 초안이 자동으로 생성됩니다."
              action={<Button onClick={() => navigate("/projects/new")}>새 프로젝트</Button>}
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
