import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { useAuth } from "@/hooks/useAuth";

export type UserRole = "viewer" | "worker" | "admin" | "super_admin";

const ROLE_RANK: Record<UserRole, number> = {
  viewer: 1,
  worker: 2,
  admin: 3,
  super_admin: 4,
};

export function hasRoleAtLeast(role: UserRole, atLeast: UserRole): boolean {
  return ROLE_RANK[role] >= ROLE_RANK[atLeast];
}

export interface RequireAuthProps {
  minRole?: UserRole;
  children: ReactNode;
}

export function RequireAuth({ minRole, children }: RequireAuthProps) {
  const { user, isLoading } = useAuth();
  const location = useLocation();

  if (isLoading) return <LoadingSkeleton variant="block" />;
  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }
  if (minRole && !hasRoleAtLeast(user.role, minRole)) {
    return <Navigate to="/403" replace />;
  }

  return <>{children}</>;
}
