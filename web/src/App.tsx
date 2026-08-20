import { lazy, Suspense } from "react";
import {
  createBrowserRouter,
  Navigate,
  RouterProvider,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { RequireAuth } from "@/components/auth/RequireAuth";
import { Toaster } from "@/components/ui/sonner";
import ForbiddenPage from "@/pages/403";
import AdminDashboardPage from "@/pages/admin/dashboard";
import AdminGpuPage from "@/pages/admin/gpu";
import AdminIpPage from "@/pages/admin/ip";
import AdminSettingsPage from "@/pages/admin/settings";
import AdminTokensPage from "@/pages/admin/tokens";
import AdminUsersPage from "@/pages/admin/users";
import CallbackPage from "@/pages/callback";
import LibraryPage from "@/pages/library";
import LoginPage from "@/pages/login";
import ProfilePage from "@/pages/profile";
import ProjectsPage from "@/pages/projects";
import BriefPage from "@/pages/projects/[id]/brief";
import OverviewPage from "@/pages/projects/[id]/overview";
import PreviewPage from "@/pages/projects/[id]/preview";
import QaSelectPage from "@/pages/projects/[id]/qa-select";
import SourcesPage from "@/pages/projects/[id]/sources";
import NewProjectPage from "@/pages/projects/new";
import PromptsPage from "@/pages/prompts";

// 진행 페이지는 폐지됐다(개요의 진행 단계 스테퍼로 통합) - 옛 링크는 개요로 넘긴다.
// 출력 페이지도 폐지됐다(개요 헤더의 즉시 다운로드 + 요약 카드로 통합, 2026-08-04).
function OverviewRedirect() {
  const { id } = useParams<{ id: string }>();
  return <Navigate to={`/projects/${id}/overview`} replace />;
}

// 편집기는 미리보기·편집 화면으로 통합됐다 - 기존 /editor 링크는 /preview로 넘긴다.
function EditorRedirect() {
  const { id } = useParams<{ id: string }>();
  const [sp] = useSearchParams();
  const section = sp.get("section");
  const to = `/projects/${id}/preview${section ? `?section=${section}` : ""}`;
  return <Navigate to={to} replace />;
}

const routes = [
  {
    path: "/",
    element: <Navigate to="/projects" replace />,
  },
  {
    path: "/login",
    element: <LoginPage />,
  },
  {
    path: "/callback",
    element: <CallbackPage />,
  },
  {
    path: "/projects",
    element: (
      <RequireAuth>
        <ProjectsPage />
      </RequireAuth>
    ),
  },
  {
    path: "/library",
    element: (
      <RequireAuth>
        <LibraryPage />
      </RequireAuth>
    ),
  },
  {
    path: "/prompts",
    element: (
      <RequireAuth minRole="worker">
        <PromptsPage />
      </RequireAuth>
    ),
  },
  {
    path: "/profile",
    element: (
      <RequireAuth>
        <ProfilePage />
      </RequireAuth>
    ),
  },
  {
    path: "/projects/new",
    element: (
      <RequireAuth>
        <NewProjectPage />
      </RequireAuth>
    ),
  },
  {
    path: "/projects/:id/overview",
    element: (
      <RequireAuth>
        <OverviewPage />
      </RequireAuth>
    ),
  },
  {
    path: "/projects/:id/brief",
    element: (
      <RequireAuth>
        <BriefPage />
      </RequireAuth>
    ),
  },
  {
    path: "/projects/:id/sources",
    element: (
      <RequireAuth>
        <SourcesPage />
      </RequireAuth>
    ),
  },
  {
    path: "/projects/:id/qa-select",
    element: (
      <RequireAuth>
        <QaSelectPage />
      </RequireAuth>
    ),
  },
  {
    path: "/projects/:id/export",
    element: (
      <RequireAuth>
        <OverviewRedirect />
      </RequireAuth>
    ),
  },
  {
    path: "/projects/:id/editor",
    element: (
      <RequireAuth>
        <EditorRedirect />
      </RequireAuth>
    ),
  },
  {
    path: "/projects/:id/preview",
    element: (
      <RequireAuth>
        <PreviewPage />
      </RequireAuth>
    ),
  },
  {
    path: "/projects/:id/progress",
    element: (
      <RequireAuth>
        <OverviewRedirect />
      </RequireAuth>
    ),
  },
  {
    path: "/admin/dashboard",
    element: (
      <RequireAuth minRole="admin">
        <AdminDashboardPage />
      </RequireAuth>
    ),
  },
  {
    path: "/admin/gpu",
    element: (
      <RequireAuth minRole="admin">
        <AdminGpuPage />
      </RequireAuth>
    ),
  },
  {
    path: "/admin/users",
    element: (
      <RequireAuth minRole="admin">
        <AdminUsersPage />
      </RequireAuth>
    ),
  },
  {
    path: "/admin/tokens",
    element: (
      <RequireAuth minRole="admin">
        <AdminTokensPage />
      </RequireAuth>
    ),
  },
  {
    path: "/admin/ip",
    element: (
      <RequireAuth minRole="super_admin">
        <AdminIpPage />
      </RequireAuth>
    ),
  },
  {
    path: "/admin/settings",
    element: (
      <RequireAuth minRole="super_admin">
        <AdminSettingsPage />
      </RequireAuth>
    ),
  },
  {
    path: "/403",
    element: <ForbiddenPage />,
  },
];

if (import.meta.env.DEV) {
  const ComponentsGalleryPage = lazy(() => import("@/pages/__components"));
  routes.push({
    path: "/__components",
    element: (
      <Suspense fallback={<div className="p-8 text-fg-tertiary">Loading…</div>}>
        <ComponentsGalleryPage />
      </Suspense>
    ),
  });
}

const router = createBrowserRouter(routes);

export function App() {
  return (
    <>
      <RouterProvider router={router} />
      <Toaster />
    </>
  );
}
