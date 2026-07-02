import type { User } from "@/api/types";

export const DEMO_ADMIN_USER: User = {
  id: "u_admin_001",
  email: "admin@loweninsight.kr",
  name: "관리자",
  role: "admin",
  is_active: true,
  has_password: true,
  last_login_at: "2026-06-29T08:30:00Z",
  password_changed_at: "2026-04-02T09:00:00Z",
  created_at: "2026-01-15T09:00:00Z",
};

export const DEMO_CREDENTIALS = {
  id: "admin@loweninsight.kr",
  password: "demo1234",
} as const;
