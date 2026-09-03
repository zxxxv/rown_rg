import type { UserRoleType } from "@/api/types";

/** 역할 → 한글 라벨 단일 진실 - 화면마다 사본이 있어 표기가 갈라질 수 있었다. */
export const ROLE_LABEL: Record<UserRoleType, string> = {
  super_admin: "최고관리자",
  admin: "관리자",
  worker: "작성자",
  viewer: "뷰어",
};

/** 느슨한 조회 - 서버가 모르는 역할 값을 보내면 원문 그대로(거짓 라벨보다 낫다). */
export function roleLabel(role: string): string {
  return (ROLE_LABEL as Record<string, string>)[role] ?? role;
}
