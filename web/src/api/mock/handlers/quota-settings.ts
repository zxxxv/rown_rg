import { HttpResponse, http } from "msw";
import { applyQuotaPatch, listQuotaSettings } from "@/api/mock/fixtures/quota-settings";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

export const quotaSettingsHandlers = [
  http.get(url("admin/quota-settings"), () => {
    return HttpResponse.json(listQuotaSettings(), { status: 200 });
  }),

  http.patch(url("admin/quota-settings"), async ({ request }) => {
    const patch = (await request.json()) as Record<string, string>;
    return HttpResponse.json(applyQuotaPatch(patch), { status: 200 });
  }),
];
