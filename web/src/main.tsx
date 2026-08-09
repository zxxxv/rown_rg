import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "@/App";
import { env } from "@/env";
import "@/styles/global.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (count, err) => {
        const status = (err as { status?: number } | null)?.status;
        if (status === 401 || status === 403) return false;
        return count < 2;
      },
    },
  },
});

async function startMockingIfDev() {
  // VITE_USE_MOCK=false pnpm dev → MSW 없이 실백엔드(vite proxy /api → :8000)를 친다.
  if (!(import.meta.env.DEV && env.VITE_USE_MOCK !== "false")) return;
  const { worker } = await import("@/api/mock/browser");
  await worker.start({ onUnhandledRequest: "bypass" });
}

async function bootstrap() {
  await startMockingIfDev();

  const rootElement = document.getElementById("root");
  if (!rootElement) {
    throw new Error("Root element #root not found");
  }

  createRoot(rootElement).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </StrictMode>,
  );
}

void bootstrap();
