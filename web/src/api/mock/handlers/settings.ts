import { HttpResponse, http } from "msw";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

interface SettingItem {
  key: string;
  label: string;
  group: string;
  kind: "str" | "text" | "number" | "bool" | "enum";
  is_secret: boolean;
  configured: boolean;
  source: "db" | "env" | "none";
  value: string | null;
  options?: { value: string; label: string }[];
}

// 실계약 미러(GET /admin/settings) - 그룹·종류·시크릿 마스킹이 화면에 다 보이도록 대표값만.
const SETTINGS: SettingItem[] = [
  {
    key: "ANTHROPIC_API_KEY",
    label: "Anthropic API 키",
    group: "LLM",
    kind: "str",
    is_secret: true,
    configured: true,
    source: "db",
    value: "sk-ant-****",
  },
  {
    key: "OPENAI_API_KEY",
    label: "OpenAI API 키",
    group: "LLM",
    kind: "str",
    is_secret: true,
    configured: true,
    source: "env",
    value: "sk-****",
  },
  {
    key: "GEMINI_API_KEY",
    label: "Gemini API 키",
    group: "LLM",
    kind: "str",
    is_secret: true,
    configured: false,
    source: "none",
    value: null,
  },
  {
    key: "NAVERWORKS_SSO_ENABLED",
    label: "네이버웍스 SSO 사용",
    group: "네이버웍스",
    kind: "bool",
    is_secret: false,
    configured: true,
    source: "db",
    value: "true",
  },
  {
    key: "NAVERWORKS_IDP_CERT",
    label: "IdP 인증서(PEM)",
    group: "네이버웍스",
    kind: "text",
    is_secret: true,
    configured: true,
    source: "db",
    value: "-----BEGIN CERTIFICATE-----****",
  },
  {
    key: "QUOTA_ENFORCEMENT_ENABLED",
    label: "비용 한도 강제",
    group: "운영",
    kind: "bool",
    is_secret: false,
    configured: true,
    source: "env",
    value: "true",
  },
  {
    key: "WRITE_CONCURRENCY",
    label: "절 작성 동시 실행 수",
    group: "운영",
    kind: "number",
    is_secret: false,
    configured: true,
    source: "db",
    value: "4",
  },
];

const overrides = new Map<string, string>();

export const settingsHandlers = [
  http.get(url("admin/settings"), () => {
    const items = SETTINGS.map((s) =>
      overrides.has(s.key)
        ? { ...s, value: s.is_secret ? "****" : (overrides.get(s.key) ?? null), source: "db" }
        : s,
    );
    return HttpResponse.json({ data: { items } }, { status: 200 });
  }),

  http.put(url("admin/settings/:key"), async ({ params, request }) => {
    const key = String(params.key);
    const item = SETTINGS.find((s) => s.key === key);
    if (!item) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "설정을 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    const body = (await request.json()) as { value?: string };
    overrides.set(key, body.value ?? "");
    return HttpResponse.json(
      {
        data: {
          ...item,
          configured: true,
          source: "db",
          value: item.is_secret ? "****" : (body.value ?? null),
        },
      },
      { status: 200 },
    );
  }),
];
