import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  authKeys,
  login as loginRequest,
  logout as logoutRequest,
  ssoLogin as ssoLoginRequest,
  useMe,
} from "@/api/auth";
import { setForbiddenHandler, setUnauthorizedHandler } from "@/api/client";
import type { LoginInput, User } from "@/api/types";

export interface UseAuthValue {
  user: User | null;
  isLoading: boolean;
  login: (input: LoginInput) => Promise<User>;
  loginWithSso: (provider: string) => Promise<User>;
  logout: () => Promise<void>;
}

export function useAuth(): UseAuthValue {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const meQuery = useMe();

  const login = useCallback(
    async (input: LoginInput) => {
      const { user } = await loginRequest(input);
      qc.setQueryData(authKeys.me(), user);
      await qc.invalidateQueries({ queryKey: authKeys.me() });
      return user;
    },
    [qc],
  );

  const loginWithSso = useCallback(
    async (provider: string) => {
      const { user } = await ssoLoginRequest(provider);
      qc.setQueryData(authKeys.me(), user);
      await qc.invalidateQueries({ queryKey: authKeys.me() });
      return user;
    },
    [qc],
  );

  const logout = useCallback(async () => {
    try {
      await logoutRequest();
    } finally {
      qc.clear();
      navigate("/login", { replace: true });
    }
  }, [qc, navigate]);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      qc.setQueryData(authKeys.me(), null);
      qc.clear();
      navigate("/login", { replace: true });
    });
    setForbiddenHandler(() => {
      navigate("/403", { replace: true });
    });
    return () => {
      setUnauthorizedHandler(null);
      setForbiddenHandler(null);
    };
  }, [qc, navigate]);

  return {
    user: meQuery.data ?? null,
    isLoading: meQuery.isLoading,
    login,
    loginWithSso,
    logout,
  };
}
