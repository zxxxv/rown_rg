import type { User } from "@/api/types";

export const DEMO_ADMIN_USER: User = {
  id: "u_admin_001",
  email: "admin@loweninsight.kr",
  name: "관리자",
  role: "admin",
};

export const DEMO_CREDENTIALS = {
  id: "admin@loweninsight.kr",
  password: "demo1234",
} as const;
