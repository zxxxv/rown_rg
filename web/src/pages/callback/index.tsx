import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { authKeys, me } from "@/api/auth";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";

/**
 * 네이버웍스 SSO 콜백 착지점.
 * 백엔드 ACS가 HttpOnly 쿠키를 심고 여기로 리다이렉트한다.
 * 쿠키로 /auth/me 를 한 번 확인해 세션을 채운 뒤 목적지로 이동.
 */
export default function CallbackPage() {
  const qc = useQueryClient();
  const navigate = useNavigate();

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const user = await me();
        qc.setQueryData(authKeys.me(), user);
        if (active) navigate("/projects", { replace: true });
      } catch {
        if (active) navigate("/login", { replace: true });
      }
    })();
    return () => {
      active = false;
    };
  }, [qc, navigate]);

  return <LoadingSkeleton variant="block" />;
}
