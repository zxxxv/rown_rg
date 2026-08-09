import { z } from "zod";

const EnvSchema = z.object({
  VITE_API_BASE_URL: z.string().min(1).default("/api/v1"),
  VITE_WS_BASE_URL: z.string().min(1).default("/ws"),
  // dev에서 MSW 사용 여부 - "false"면 목킹을 끄고 실백엔드(vite proxy /api → :8000)를 사용
  VITE_USE_MOCK: z.string().optional(),
});

const parsed = EnvSchema.safeParse(import.meta.env);

if (!parsed.success) {
  console.error("Invalid environment variables:", parsed.error.flatten().fieldErrors);
  throw new Error("Invalid environment variables. Check console for details.");
}

export const env = parsed.data;
export type Env = z.infer<typeof EnvSchema>;
