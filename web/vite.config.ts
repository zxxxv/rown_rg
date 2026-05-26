import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";
import { z } from "zod";

const EnvSchema = z.object({
  VITE_API_BASE_URL: z.string().min(1).optional(),
  VITE_WS_BASE_URL: z.string().min(1).optional(),
});

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const parsed = EnvSchema.safeParse(env);
  if (!parsed.success) {
    throw new Error(
      `Invalid import.meta.env:\n${JSON.stringify(parsed.error.flatten().fieldErrors, null, 2)}`,
    );
  }

  return {
    plugins: [react()],
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "src"),
      },
    },
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: "http://localhost:8000",
          changeOrigin: true,
        },
        "/ws": {
          target: "ws://localhost:8000",
          ws: true,
          changeOrigin: true,
        },
      },
    },
  };
});
