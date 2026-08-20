import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/api/client";

/** gpu_service /health의 queue 스냅샷 — 필드 정의는 gpu_service/app/gpu_queue.py */
export interface GpuQueueSnapshot {
  in_flight: number;
  estimated_wait_s: number;
  max_wait_s: number;
  avg_task_s: number;
  completed_total: number;
  last_wait_ms: number;
  rejected_total: number;
}

export interface GpuHwMetrics {
  name: string;
  utilization_pct: number;
  memory_used_mib: number;
  memory_total_mib: number;
  temperature_c: number;
  power_w: number;
}

export interface GpuEmbedStatus {
  ready: boolean;
  on_gpu: boolean;
  model_dir: string;
  dimension: number;
  max_chars_per_batch: number;
  warmup_ms: number | null;
}

export interface GpuParseStatus {
  ready: boolean;
  device: string;
  docling_version: string;
  artifacts_dir: string;
  /** 파싱 전용 레인 — 리랭킹·임베딩 queue와 통계가 분리돼 있다 */
  lane: GpuQueueSnapshot;
}

export interface GpuHealth {
  status: string;
  /** 서비스 기동 시각(epoch 초) — 누적 카운터의 기준점 */
  started_at?: number;
  ready: boolean;
  on_gpu: boolean;
  providers: string[];
  warmup_ms: number | null;
  queue: GpuQueueSnapshot;
  gpu: GpuHwMetrics | null;
  embed: GpuEmbedStatus | null;
  parse?: GpuParseStatus | null;
}

/** 필드별 병렬 배열 — gpu_service/app/stats_history.py의 series()와 1:1 */
export interface GpuHistory {
  interval_s: number;
  t: number[];
  in_flight: number[];
  estimated_wait_s: number[];
  avg_task_s: number[];
  completed_total: number[];
  rejected_total: number[];
  gpu_util_pct: (number | null)[];
  vram_used_mib: (number | null)[];
  temperature_c: (number | null)[];
  power_w: (number | null)[];
}

export interface RemoteClientStats {
  mode: "remote" | "local" | "unused";
  fallback_policy?: string;
  base_url?: string;
  remote_ok_total?: number;
  fallback_total?: Record<string, number>;
  fallback_items_total?: number;
  last_fallback_at?: string | null;
  last_error?: string | null;
  in_cooldown?: boolean;
  cooldown_remaining_s?: number;
}

export interface GpuMonitorData {
  gpu_service:
    | { reachable: true; configured: true; health: GpuHealth; history: GpuHistory }
    | { reachable: false; configured: boolean; error: string };
  clients: {
    reranker: RemoteClientStats;
    embedding: RemoteClientStats;
    parser?: RemoteClientStats;
  };
}

export const gpuKeys = {
  monitor: ["admin", "gpu-monitor"] as const,
};

export async function getGpuMonitor(): Promise<GpuMonitorData> {
  return apiClient.get<GpuMonitorData>("admin/gpu");
}

export function useGpuMonitor() {
  return useQuery({
    queryKey: gpuKeys.monitor,
    queryFn: getGpuMonitor,
    // 라이브 화면이다. 10초면 5초 샘플 두 칸 - 더 조여도 새 정보가 없다.
    refetchInterval: 10_000,
  });
}
