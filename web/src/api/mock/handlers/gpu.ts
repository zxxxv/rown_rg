import { HttpResponse, http } from "msw";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

// 실계약 미러(GET /admin/gpu) - src/api/gpu.ts GpuMonitorData와 1:1.
// 12칸(5초 간격 1분) 시계열로 차트가 그려지는 최소 데모.
const N = 12;
const seq = (base: number, step: number) => Array.from({ length: N }, (_, i) => base + i * step);

export const gpuHandlers = [
  http.get(url("admin/gpu"), () => {
    return HttpResponse.json(
      {
        data: {
          gpu_service: {
            reachable: true,
            configured: true,
            health: {
              status: "ok",
              ready: true,
              on_gpu: true,
              providers: ["CUDAExecutionProvider", "CPUExecutionProvider"],
              warmup_ms: 1840,
              queue: {
                in_flight: 1,
                estimated_wait_s: 0.4,
                max_wait_s: 30,
                avg_task_s: 0.38,
                completed_total: 1284,
                last_wait_ms: 120,
                rejected_total: 2,
              },
              gpu: {
                name: "NVIDIA GeForce RTX 4070",
                utilization_pct: 62,
                memory_used_mib: 5210,
                memory_total_mib: 12282,
                temperature_c: 61,
                power_w: 148,
              },
              embed: {
                ready: true,
                on_gpu: true,
                model_dir: "/models/bge-m3",
                dimension: 1024,
                max_chars_per_batch: 40000,
                warmup_ms: 2210,
              },
            },
            history: {
              interval_s: 5,
              t: seq(0, 5),
              in_flight: [0, 1, 2, 1, 3, 2, 1, 0, 1, 2, 1, 1],
              estimated_wait_s: [0, 0.2, 0.5, 0.3, 0.9, 0.6, 0.3, 0, 0.2, 0.5, 0.3, 0.4],
              avg_task_s: [0.35, 0.36, 0.38, 0.37, 0.4, 0.39, 0.38, 0.37, 0.38, 0.39, 0.38, 0.38],
              completed_total: seq(1200, 7),
              rejected_total: Array.from({ length: N }, () => 2),
              gpu_util_pct: [40, 55, 70, 62, 80, 75, 60, 45, 58, 66, 61, 62],
              vram_used_mib: seq(5000, 20),
              temperature_c: [58, 59, 61, 60, 63, 62, 61, 59, 60, 61, 61, 61],
              power_w: [120, 135, 150, 145, 160, 155, 148, 130, 142, 150, 147, 148],
            },
          },
          clients: {
            reranker: {
              mode: "remote",
              fallback_policy: "local",
              base_url: "http://gpu-box:8100",
              remote_ok_total: 412,
              fallback_total: { timeout: 3, connect: 1 },
              fallback_items_total: 96,
              last_fallback_at: "2026-05-27T02:14:00Z",
              last_error: null,
              in_cooldown: false,
              cooldown_remaining_s: 0,
            },
            embedding: {
              mode: "remote",
              fallback_policy: "local",
              base_url: "http://gpu-box:8100",
              remote_ok_total: 980,
              fallback_total: {},
              fallback_items_total: 0,
              last_fallback_at: null,
              last_error: null,
              in_cooldown: false,
              cooldown_remaining_s: 0,
            },
          },
        },
      },
      { status: 200 },
    );
  }),
];
