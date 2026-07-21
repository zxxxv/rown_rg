import { HttpResponse, http } from "msw";
import { z } from "zod";
import { createProjectFolderForLibrary } from "@/api/mock/fixtures/library";
import { DEMO_PROJECTS } from "@/api/mock/fixtures/projects";
import { DEMO_ADMIN_USER } from "@/api/mock/fixtures/users";
import type { Project, ProjectSort, ProjectStatus } from "@/api/types";
import { DepthModeSchema, ProjectConfigSchema } from "@/api/types";
import { env } from "@/env";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

const ProjectCreateBodySchema = z.object({
  title: z.string().min(1).max(255),
  topic: z.string().min(1),
  preset: z.string().max(100).nullable(),
  config: ProjectConfigSchema,
  depth_mode: DepthModeSchema,
});

function sortProjects(items: Project[], sort: ProjectSort): Project[] {
  const copy = [...items];
  copy.sort((a, b) => {
    if (sort === "title_asc") return a.title.localeCompare(b.title, "ko");
    return b.created_at.localeCompare(a.created_at);
  });
  return copy;
}

export const projectsHandlers = [
  http.get(url("projects"), ({ request }) => {
    const u = new URL(request.url);
    const status = u.searchParams.get("status") as ProjectStatus | null;
    const sort = (u.searchParams.get("sort") as ProjectSort | null) ?? "created_desc";
    const q = u.searchParams.get("q")?.trim().toLowerCase() ?? "";
    const limit = Math.max(1, Math.min(500, Number(u.searchParams.get("limit") ?? "200")));
    const offset = Math.max(0, Number(u.searchParams.get("offset") ?? "0"));

    let result = DEMO_PROJECTS as Project[];
    if (status) result = result.filter((p) => p.status === status);
    if (q) {
      result = result.filter(
        (p) => p.title.toLowerCase().includes(q) || p.topic.toLowerCase().includes(q),
      );
    }
    result = sortProjects(result, sort);
    const total = result.length;
    const items = result.slice(offset, offset + limit);

    return HttpResponse.json({ data: { items, total } }, { status: 200 });
  }),

  http.post(url("projects"), async ({ request }) => {
    const body = await request.json();
    // 백엔드 ProjectCreate 계약: preset·depth_mode 최상위 필드
    const parsed = ProjectCreateBodySchema.safeParse(body);
    if (!parsed.success) {
      return HttpResponse.json(
        {
          error: {
            code: "validation_failed",
            message: "입력값 검증에 실패했습니다.",
            details: { fieldErrors: parsed.error.flatten().fieldErrors },
          },
        },
        { status: 422 },
      );
    }

    const v = parsed.data;
    const nowIso = new Date().toISOString();
    const project: Project = {
      id: `proj_${crypto.randomUUID().slice(0, 8)}`,
      title: v.title,
      topic: v.topic,
      preset: v.preset,
      config: v.config,
      status: "draft",
      depth_mode: v.depth_mode,
      owner_id: DEMO_ADMIN_USER.id,
      created_at: nowIso,
      updated_at: nowIso,
      progress: 0,
    };
    DEMO_PROJECTS.unshift(project);
    createProjectFolderForLibrary(project);
    return HttpResponse.json({ data: project }, { status: 201 });
  }),

  // POST /projects/{id}/run — 백그라운드 실행 시작(202)
  http.post(url("projects/:id/run"), ({ params }) => {
    const id = String(params.id);
    const project = DEMO_PROJECTS.find((p) => p.id === id);
    if (!project) {
      return HttpResponse.json(
        { error: { code: "PROJECT_NOT_FOUND", message: "프로젝트를 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    if (project.status !== "draft") {
      return HttpResponse.json(
        {
          error: {
            code: "PROJECT_NOT_RUNNABLE",
            message: `실행할 수 없는 상태입니다(현재: ${project.status})`,
          },
        },
        { status: 422 },
      );
    }
    project.status = "researching";
    project.progress = 5;
    project.updated_at = new Date().toISOString();
    return HttpResponse.json(
      { data: { project_id: project.id, status: "researching" } },
      { status: 202 },
    );
  }),

  // GET /projects/{id}/export — HWPX 파일(더미 blob)
  http.get(url("projects/:id/export"), ({ params }) => {
    const id = String(params.id);
    const project = DEMO_PROJECTS.find((p) => p.id === id);
    if (!project) {
      return HttpResponse.json(
        { error: { code: "PROJECT_NOT_FOUND", message: "프로젝트를 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    const blob = new Blob([`HWPX demo file for ${project.title}`], {
      type: "application/octet-stream",
    });
    return new HttpResponse(blob, {
      status: 200,
      headers: {
        "Content-Type": "application/octet-stream",
        "Content-Disposition": `attachment; filename="${encodeURIComponent(project.title)}.hwpx"`,
      },
    });
  }),

  http.get(url("projects/:id"), ({ params }) => {
    const id = String(params.id);
    const project = DEMO_PROJECTS.find((p) => p.id === id);
    if (!project) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "프로젝트를 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    return HttpResponse.json({ data: project }, { status: 200 });
  }),

  http.patch(url("projects/:id/config"), async ({ params, request }) => {
    const id = String(params.id);
    const project = DEMO_PROJECTS.find((p) => p.id === id);
    if (!project) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "프로젝트를 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    const body = (await request.json()) as { config?: unknown };
    const parsed = ProjectConfigSchema.safeParse(body?.config);
    if (!parsed.success) {
      return HttpResponse.json(
        {
          error: {
            code: "validation_failed",
            message: "입력값 검증에 실패했습니다.",
            details: { fieldErrors: parsed.error.flatten().fieldErrors },
          },
        },
        { status: 422 },
      );
    }
    project.config = parsed.data;
    project.preset = parsed.data.preset;
    project.depth_mode = parsed.data.depth_mode;
    project.updated_at = new Date().toISOString();
    return HttpResponse.json({ data: project }, { status: 200 });
  }),
];
