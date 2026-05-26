import { HttpResponse, http } from "msw";
import { DEMO_PROJECTS } from "@/api/mock/fixtures/projects";
import { DEMO_ADMIN_USER } from "@/api/mock/fixtures/users";
import type { Project, ProjectSort, ProjectStatus } from "@/api/types";
import { ProjectConfigSchema } from "@/api/types";
import { env } from "@/env";
import { ProjectFormSchema } from "@/features/project-config/schema";

function url(path: string): string {
  const base = env.VITE_API_BASE_URL.replace(/\/$/, "");
  return `${base}/${path.replace(/^\//, "")}`;
}

function sortProjects(items: Project[], sort: ProjectSort): Project[] {
  const copy = [...items];
  copy.sort((a, b) => {
    if (sort === "title_asc") return a.title.localeCompare(b.title, "ko");
    if (sort === "deadline_asc") {
      if (!a.deadline && !b.deadline) return 0;
      if (!a.deadline) return 1;
      if (!b.deadline) return -1;
      return a.deadline.localeCompare(b.deadline);
    }
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
    const parsed = ProjectFormSchema.safeParse(body);
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
      preset: v.config.preset,
      config: v.config,
      status: "draft",
      depth_mode: v.config.depth_mode,
      owner_id: DEMO_ADMIN_USER.id,
      created_at: nowIso,
      updated_at: nowIso,
      deadline: v.deadline,
      progress: 0,
    };
    DEMO_PROJECTS.unshift(project);
    return HttpResponse.json({ data: project }, { status: 201 });
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
