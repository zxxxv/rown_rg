import { HttpResponse, http } from "msw";
import type { IpWhitelistEntry } from "@/api/ip-whitelist";
import { IP_WHITELIST } from "@/api/mock/fixtures/ip-whitelist";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

function notFound() {
  return HttpResponse.json(
    { error: { code: "IP_ENTRY_NOT_FOUND", message: "항목을 찾을 수 없습니다" } },
    { status: 404 },
  );
}

interface CreateBody {
  ip_cidr?: string;
  description?: string | null;
  expires_at?: string | null;
}

interface UpdateBody {
  description?: string | null;
  is_active?: boolean;
  expires_at?: string | null;
}

// 백엔드 _normalize_cidr의 축소판 - 단일 IP는 /32를 붙인다(형식 오류는 422).
function normalizeCidr(value: string): string | null {
  const trimmed = value.trim();
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(\/(\d{1,2}))?$/.exec(trimmed);
  if (!m) return null;
  const octets = [m[1], m[2], m[3], m[4]].map(Number);
  if (octets.some((o) => o > 255)) return null;
  const prefix = m[6] !== undefined ? Number(m[6]) : 32;
  if (prefix > 32) return null;
  return `${octets.join(".")}/${prefix}`;
}

export const ipWhitelistHandlers = [
  http.get(url("admin/ip-whitelist"), () => {
    const sorted = [...IP_WHITELIST].sort((a, b) => b.created_at.localeCompare(a.created_at));
    return HttpResponse.json({ data: sorted }, { status: 200 });
  }),

  http.post(url("admin/ip-whitelist"), async ({ request }) => {
    const body = (await request.json()) as CreateBody;
    const cidr = normalizeCidr(body.ip_cidr ?? "");
    if (!cidr) {
      return HttpResponse.json(
        { error: { code: "INVALID_CIDR", message: `유효하지 않은 IP/CIDR: ${body.ip_cidr}` } },
        { status: 422 },
      );
    }
    if (body.expires_at && new Date(body.expires_at).getTime() <= Date.now()) {
      return HttpResponse.json(
        { error: { code: "EXPIRES_IN_PAST", message: "expires_at은 미래 시각이어야 합니다" } },
        { status: 422 },
      );
    }
    if (IP_WHITELIST.some((e) => e.ip_cidr === cidr && e.is_active)) {
      return HttpResponse.json(
        { error: { code: "DUPLICATE_CIDR", message: `이미 등록된 CIDR: ${cidr}` } },
        { status: 422 },
      );
    }
    const nowIso = new Date().toISOString();
    const entry: IpWhitelistEntry = {
      id: `ip_${crypto.randomUUID().slice(0, 8)}`,
      ip_cidr: cidr,
      description: body.description ?? null,
      is_active: true,
      expires_at: body.expires_at ?? null,
      created_by: "u_admin_001",
      created_at: nowIso,
      updated_at: nowIso,
    };
    IP_WHITELIST.unshift(entry);
    return HttpResponse.json({ data: entry }, { status: 201 });
  }),

  http.patch(url("admin/ip-whitelist/:id"), async ({ params, request }) => {
    const id = String(params.id);
    const entry = IP_WHITELIST.find((e) => e.id === id);
    if (!entry) return notFound();
    const body = (await request.json()) as UpdateBody;
    if (body.description !== undefined) entry.description = body.description;
    if (body.is_active !== undefined) entry.is_active = body.is_active;
    if (body.expires_at !== undefined) entry.expires_at = body.expires_at;
    entry.updated_at = new Date().toISOString();
    return HttpResponse.json({ data: entry }, { status: 200 });
  }),

  http.delete(url("admin/ip-whitelist/:id"), ({ params }) => {
    const id = String(params.id);
    const idx = IP_WHITELIST.findIndex((e) => e.id === id);
    if (idx < 0) return notFound();
    IP_WHITELIST.splice(idx, 1);
    return HttpResponse.json({ data: { detail: "삭제되었습니다" } }, { status: 200 });
  }),
];
