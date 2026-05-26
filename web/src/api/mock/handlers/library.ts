import { HttpResponse, http } from "msw";
import { LIBRARY_TREE } from "@/api/mock/fixtures/library";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

export const libraryHandlers = [
  http.get(url("library/tree"), () => {
    return HttpResponse.json({ data: { tree: LIBRARY_TREE } }, { status: 200 });
  }),
];
