import { HttpResponse, http } from "msw";
import { DEMO_PRESETS } from "@/api/mock/fixtures/presets";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

export const presetsHandlers = [
  http.get(url("presets"), () => {
    return HttpResponse.json({ data: { items: DEMO_PRESETS } }, { status: 200 });
  }),
];
