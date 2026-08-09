import type { IpWhitelistEntry } from "@/api/ip-whitelist";

function hoursFromNow(h: number): string {
  return new Date(Date.now() + h * 3_600_000).toISOString();
}

// IP 화이트리스트 픽스처 - 핸들러가 직접 변형하는 in-memory 저장소.
export const IP_WHITELIST: IpWhitelistEntry[] = [
  {
    id: "ip_001",
    ip_cidr: "211.234.100.0/24",
    description: "본사 사무실",
    is_active: true,
    expires_at: null,
    created_by: "u_admin_001",
    created_at: "2026-04-01T00:00:00Z",
    updated_at: "2026-04-01T00:00:00Z",
  },
  {
    id: "ip_002",
    ip_cidr: "10.8.0.0/16",
    description: "사내 VPN 대역",
    is_active: true,
    expires_at: null,
    created_by: "u_admin_001",
    created_at: "2026-04-03T00:00:00Z",
    updated_at: "2026-04-03T00:00:00Z",
  },
  {
    id: "ip_003",
    ip_cidr: "203.0.113.87/32",
    description: "외부 감사인 임시 접속",
    is_active: true,
    expires_at: hoursFromNow(48),
    created_by: "u_admin_001",
    created_at: "2026-05-20T00:00:00Z",
    updated_at: "2026-05-20T00:00:00Z",
  },
  {
    id: "ip_004",
    ip_cidr: "198.51.100.14/32",
    description: "퇴사자 재택 IP (차단)",
    is_active: false,
    expires_at: null,
    created_by: "u_admin_001",
    created_at: "2026-02-11T00:00:00Z",
    updated_at: "2026-05-02T00:00:00Z",
  },
];
