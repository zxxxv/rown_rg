import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  MemoryStick,
  RefreshCw,
  ShieldAlert,
  Thermometer,
  Timer,
} from "lucide-react";
import { useMemo } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  type GpuHistory,
  type GpuMonitorData,
  type RemoteClientStats,
  useGpuMonitor,
} from "@/api/gpu";
import { StatCard, type StatTone } from "@/components/data-display/StatCard";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";

const ACCENT = "var(--color-accent)";
const WARNING = "var(--color-text-warning)";
const DANGER = "var(--color-text-danger)";

const GRID_PROPS = {
  strokeDasharray: "3 3",
  stroke: "var(--color-border-tertiary)",
  vertical: false,
} as const;

const AXIS_TICK = { fontSize: 11, fill: "var(--color-text-tertiary)" } as const;

const TOOLTIP_STYLE = {
  backgroundColor: "var(--color-background-primary)",
  border: "1px solid var(--color-border-primary)",
  borderRadius: 6,
  fontSize: 12,
} as const;

export default function AdminGpuPage() {
  const { user, logout } = useAuth();
  const monitor = useGpuMonitor();

  return (
    <AppShell
      user={user ? { name: user.name, role: user.role } : null}
      onLogout={() => void logout()}
    >
      <div className="flex flex-col gap-6">
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-3xl font-semibold text-fg">GPU 추론 모니터</h1>
            <p className="text-sm text-fg-secondary">
              집 GPU 박스(RTX 3060 Ti)의 큐·하드웨어 상태와 앱 폴백 현황 — 10초마다 갱신
            </p>
          </div>
          {monitor.data ? <ServiceBadge data={monitor.data} /> : null}
        </header>

        {monitor.isLoading ? (
          <LoadingSkeleton variant="card" count={4} />
        ) : monitor.isError || !monitor.data ? (
          <EmptyState
            title="모니터 데이터를 불러오지 못했습니다"
            description="잠시 후 다시 시도해 주세요."
          />
        ) : (
          <MonitorBody data={monitor.data} />
        )}
      </div>
    </AppShell>
  );
}

function ServiceBadge({ data }: { data: GpuMonitorData }) {
  const svc = data.gpu_service;
  if (svc.reachable) {
    const onGpu = svc.health.on_gpu && (svc.health.embed?.on_gpu ?? true);
    return onGpu ? (
      <Badge className="gap-1.5 bg-bg-success text-fg-success">
        <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> 온라인 · GPU 동작 중
      </Badge>
    ) : (
      <Badge className="gap-1.5 bg-bg-danger text-fg-danger">
        <AlertTriangle className="h-3.5 w-3.5" aria-hidden /> 온라인 · CPU로 떨어짐
      </Badge>
    );
  }
  return (
    <Badge className="gap-1.5 bg-bg-danger text-fg-danger">
      <AlertTriangle className="h-3.5 w-3.5" aria-hidden />
      {svc.configured ? "GPU 박스 연결 안 됨" : "원격 GPU 미설정"}
    </Badge>
  );
}

function MonitorBody({ data }: { data: GpuMonitorData }) {
  const svc = data.gpu_service;

  return (
    <>
      {svc.reachable ? (
        <>
          <StatTiles data={data} />
          <ChartGrid history={svc.history} maxWaitS={svc.health.queue.max_wait_s} />
        </>
      ) : (
        <Card className="border-fg-danger/30 bg-bg-danger/40">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-fg-danger">
              <AlertTriangle className="h-5 w-5" aria-hidden /> GPU 박스에 닿지 않습니다
            </CardTitle>
            <CardDescription className="break-all">
              {svc.configured
                ? `터널 또는 서비스가 내려간 상태입니다. 요청은 CPU 폴백으로 처리됩니다 — ${"error" in svc ? svc.error : ""}`
                : "원격 GPU가 설정되지 않은 배포입니다."}
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <FallbackCard title="리랭커 폴백" stats={data.clients.reranker} itemNoun="후보" />
        <FallbackCard
          title="임베딩 폴백"
          stats={data.clients.embedding}
          itemNoun="텍스트"
          itemsAreSevere
        />
      </section>
    </>
  );
}

function StatTiles({ data }: { data: GpuMonitorData }) {
  const svc = data.gpu_service;
  if (!svc.reachable) return null;
  const { queue, gpu, embed, on_gpu } = svc.health;

  const tempTone: StatTone =
    gpu == null ? "default" : gpu.temperature_c >= 83 ? "danger" : gpu.temperature_c >= 75 ? "warning" : "default";
  const waitTone: StatTone =
    queue.estimated_wait_s >= queue.max_wait_s
      ? "danger"
      : queue.estimated_wait_s >= queue.max_wait_s / 2
        ? "warning"
        : "default";

  return (
    <section className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <StatCard
        label="GPU 사용률"
        value={gpu ? `${gpu.utilization_pct}%` : "—"}
        icon={Cpu}
        tone={on_gpu ? "default" : "danger"}
        hint={on_gpu && (embed?.on_gpu ?? true) ? "리랭커·임베딩 CUDA" : "CPU 실행 중 — 확인 필요"}
      />
      <StatCard
        label="VRAM"
        value={gpu ? `${(gpu.memory_used_mib / 1024).toFixed(1)}G` : "—"}
        icon={MemoryStick}
        hint={gpu ? `/ ${(gpu.memory_total_mib / 1024).toFixed(0)}GB` : undefined}
        progress={gpu ? { current: gpu.memory_used_mib, max: gpu.memory_total_mib } : undefined}
      />
      <StatCard
        label="온도"
        value={gpu ? `${gpu.temperature_c}°C` : "—"}
        icon={Thermometer}
        tone={tempTone}
        hint={gpu ? `${gpu.power_w}W` : undefined}
      />
      <StatCard
        label="예상 대기"
        value={`${queue.estimated_wait_s.toFixed(1)}s`}
        icon={Timer}
        tone={waitTone}
        hint={`상한 ${queue.max_wait_s.toFixed(0)}s · 평균 처리 ${queue.avg_task_s.toFixed(1)}s`}
      />
      <StatCard
        label="처리 완료"
        value={queue.completed_total.toLocaleString()}
        icon={CheckCircle2}
        hint="기동 이후 누적"
      />
      <StatCard
        label="429 거절"
        value={queue.rejected_total.toLocaleString()}
        icon={ShieldAlert}
        tone={queue.rejected_total > 0 ? "warning" : "default"}
        hint={queue.rejected_total > 0 ? "용량 재검토 신호" : "정상"}
      />
    </section>
  );
}

/** 병렬 배열 이력을 recharts 행 배열로 — 누적 카운터는 이웃 칸 차로 분당 rate를 만든다. */
function useChartRows(history: GpuHistory) {
  return useMemo(() => {
    const perMin = 60 / history.interval_s;
    return history.t.map((t, i) => {
      const time = new Date(t * 1000).toLocaleTimeString("ko-KR", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
      const util = history.gpu_util_pct[i];
      const vram = history.vram_used_mib[i];
      const total = 8192; // RTX 3060 Ti 고정 - health에 있지만 이력엔 없다
      const prevCompleted = i > 0 ? history.completed_total[i - 1] : history.completed_total[i];
      const prevRejected = i > 0 ? history.rejected_total[i - 1] : history.rejected_total[i];
      return {
        time,
        util: util ?? undefined,
        vramPct: vram == null ? undefined : Math.round((vram / total) * 100),
        wait: history.estimated_wait_s[i],
        completedPerMin: Math.max(0, history.completed_total[i] - prevCompleted) * perMin,
        rejectedPerMin: Math.max(0, history.rejected_total[i] - prevRejected) * perMin,
      };
    });
  }, [history]);
}

function ChartGrid({ history, maxWaitS }: { history: GpuHistory; maxWaitS: number }) {
  const rows = useChartRows(history);
  if (rows.length < 2) {
    return (
      <Card>
        <CardContent className="py-8 text-center text-sm text-fg-tertiary">
          이력이 아직 쌓이는 중입니다 — 잠시 후 그래프가 나타납니다.
        </CardContent>
      </Card>
    );
  }
  const tickGap = Math.max(1, Math.floor(rows.length / 6));

  return (
    <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <ChartCard title="GPU 사용률 · VRAM" description="최근 1시간, 5초 간격 (%)">
        <AreaChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="utilGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={ACCENT} stopOpacity={0.35} />
              <stop offset="95%" stopColor={ACCENT} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis dataKey="time" tick={AXIS_TICK} interval={tickGap} stroke="var(--color-border-primary)" />
          <YAxis
            tick={AXIS_TICK}
            stroke="var(--color-border-primary)"
            domain={[0, 100]}
            tickFormatter={(v) => `${v}%`}
            width={40}
          />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            formatter={(value, name) => [
              `${typeof value === "number" ? Math.round(value) : value}%`,
              name === "util" ? "GPU 사용률" : "VRAM",
            ]}
          />
          <Legend
            formatter={(v) => (v === "util" ? "GPU 사용률" : "VRAM")}
            wrapperStyle={{ fontSize: 12 }}
          />
          <Area type="monotone" dataKey="util" stroke={ACCENT} strokeWidth={2} fill="url(#utilGradient)" />
          <Area
            type="monotone"
            dataKey="vramPct"
            stroke={WARNING}
            strokeWidth={1.5}
            fill="none"
            strokeDasharray="3 3"
          />
        </AreaChart>
      </ChartCard>

      <ChartCard title="큐 예상 대기" description={`상한 ${maxWaitS.toFixed(0)}초를 넘으면 429로 거절`}>
        <AreaChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="waitGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={ACCENT} stopOpacity={0.35} />
              <stop offset="95%" stopColor={ACCENT} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis dataKey="time" tick={AXIS_TICK} interval={tickGap} stroke="var(--color-border-primary)" />
          <YAxis
            tick={AXIS_TICK}
            stroke="var(--color-border-primary)"
            tickFormatter={(v) => `${v}s`}
            width={40}
          />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            formatter={(value) => [`${typeof value === "number" ? value.toFixed(1) : value}초`, "예상 대기"]}
          />
          <ReferenceLine y={maxWaitS} stroke={DANGER} strokeDasharray="4 4" />
          <Area type="monotone" dataKey="wait" stroke={ACCENT} strokeWidth={2} fill="url(#waitGradient)" />
        </AreaChart>
      </ChartCard>

      <ChartCard
        title="처리량 · 거절"
        description="분당 건수 (5초 샘플 차분)"
        className="lg:col-span-2"
      >
        <BarChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
          <CartesianGrid {...GRID_PROPS} />
          <XAxis dataKey="time" tick={AXIS_TICK} interval={tickGap} stroke="var(--color-border-primary)" />
          <YAxis tick={AXIS_TICK} stroke="var(--color-border-primary)" width={40} allowDecimals={false} />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            formatter={(value, name) => [
              `${typeof value === "number" ? Math.round(value) : value}건/분`,
              name === "completedPerMin" ? "처리" : "429 거절",
            ]}
          />
          <Legend
            formatter={(v) => (v === "completedPerMin" ? "처리" : "429 거절")}
            wrapperStyle={{ fontSize: 12 }}
          />
          <Bar dataKey="completedPerMin" fill={ACCENT} radius={[4, 4, 0, 0]} />
          <Bar dataKey="rejectedPerMin" fill={DANGER} radius={[4, 4, 0, 0]} />
        </BarChart>
      </ChartCard>
    </section>
  );
}

function ChartCard({
  title,
  description,
  className,
  children,
}: {
  title: string;
  description: string;
  className?: string;
  children: React.ReactElement;
}) {
  return (
    <Card className={className}>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="h-52 sm:h-60">
          <ResponsiveContainer width="100%" height="100%">
            {children}
          </ResponsiveContainer>
        </div>
      </CardContent>
    </Card>
  );
}

function FallbackCard({
  title,
  stats,
  itemNoun,
  itemsAreSevere = false,
}: {
  title: string;
  stats: RemoteClientStats;
  itemNoun: string;
  // 임베딩만 true - 폴백으로 만든 벡터는 dtype이 달라 색인 품질을 해친다.
  itemsAreSevere?: boolean;
}) {
  if (stats.mode === "unused") {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{title}</CardTitle>
          <CardDescription>아직 호출되지 않았습니다 (기동 이후 사용 없음)</CardDescription>
        </CardHeader>
      </Card>
    );
  }
  if (stats.mode === "local") {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">{title}</CardTitle>
          <CardDescription>로컬 CPU 모드로 동작 중 — 원격 GPU 미사용</CardDescription>
        </CardHeader>
      </Card>
    );
  }

  const fallbacks = Object.values(stats.fallback_total ?? {}).reduce((a, b) => a + b, 0);
  const severe = itemsAreSevere && (stats.fallback_items_total ?? 0) > 0;

  return (
    <Card className={cn(severe && "border-fg-warning/30")}>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle className="text-base">{title}</CardTitle>
          {stats.in_cooldown ? (
            <Badge className="bg-bg-warning text-fg-warning">
              쿨다운 {stats.cooldown_remaining_s?.toFixed(0)}초 — CPU로 처리 중
            </Badge>
          ) : (
            <Badge className="bg-bg-success text-fg-success">원격 사용 중</Badge>
          )}
        </div>
        <CardDescription>
          폴백 정책: {stats.fallback_policy} · 성공 {stats.remote_ok_total?.toLocaleString()}회
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-sm">
        <div className="flex flex-wrap gap-x-6 gap-y-1">
          <span className="text-fg-secondary">
            폴백 <b className={cn("font-mono", fallbacks > 0 ? "text-fg-warning" : "text-fg")}>{fallbacks}</b>회
            {stats.fallback_total?.cooldown ? ` (쿨다운 ${stats.fallback_total.cooldown})` : null}
          </span>
          <span className="text-fg-secondary">
            폴백 처리 {itemNoun}{" "}
            <b className={cn("font-mono", severe ? "text-fg-warning" : "text-fg")}>
              {(stats.fallback_items_total ?? 0).toLocaleString()}
            </b>
            개
          </span>
        </div>
        {severe ? (
          <p className="flex items-start gap-1.5 text-xs text-fg-warning">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
            CPU 폴백으로 만든 벡터가 색인에 들어갔을 수 있습니다 — 재색인 검토가 필요합니다.
          </p>
        ) : null}
        {stats.last_fallback_at ? (
          <p className="text-xs text-fg-tertiary">
            마지막 폴백 {new Date(stats.last_fallback_at).toLocaleString("ko-KR")}
            {stats.last_error ? ` · ${stats.last_error}` : null}
          </p>
        ) : (
          <p className="flex items-center gap-1.5 text-xs text-fg-tertiary">
            <RefreshCw className="h-3 w-3" aria-hidden /> 기동 이후 폴백 없음
          </p>
        )}
      </CardContent>
    </Card>
  );
}
