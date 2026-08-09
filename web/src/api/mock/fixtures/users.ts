import type { User } from "@/api/types";

// super_admin: /admin/ip(최고관리자 전용) 화면까지 MSW로 렌더 가능하도록 상향
export const DEMO_ADMIN_USER: User = {
  id: "u_admin_001",
  email: "admin@loweninsight.kr",
  username: "admin_rown",
  name: "관리자",
  role: "super_admin",
  is_active: true,
  has_password: true,
  last_login_at: "2026-06-29T08:30:00Z",
  password_changed_at: "2026-04-02T09:00:00Z",
  created_at: "2026-01-15T09:00:00Z",
};

export const DEMO_CREDENTIALS = {
  username: "admin_rown",
  email: "admin@loweninsight.kr",
  password: "Admin_rown12!",
} as const;
