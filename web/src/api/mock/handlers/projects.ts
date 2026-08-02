import { HttpResponse, http } from "msw";
import { z } from "zod";
import { createProjectFolderForLibrary } from "@/api/mock/fixtures/library";
import { DEMO_PROJECTS } from "@/api/mock/fixtures/projects";
import { DEMO_ADMIN_USER } from "@/api/mock/fixtures/users";
import type { Project } from "@/api/types";
import { DepthModeSchema, ProjectConfigSchema, ProjectStatusSchema } from "@/api/types";
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

export const projectsHandlers = [
  // 실계약: GET /projects?limit&offset&status&q → ProjectRead[] (봉투·total 없음, 최신순 고정)
  //   status = ProjectStage 값(오값 422), q = 제목·주제 부분검색
  http.get(url("projects"), ({ request }) => {
    const u = new URL(request.url);
    const limit = Math.max(1, Math.min(500, Number(u.searchParams.get("limit") ?? "50")));
    const offset = Math.max(0, Number(u.searchParams.get("offset") ?? "0"));
    const statusRaw = u.searchParams.get("status");
    const q = u.searchParams.get("q")?.trim().toLowerCase() ?? "";

    if (statusRaw !== null && !ProjectStatusSchema.safeParse(statusRaw).success) {
      return HttpResponse.json(
        {
          error: {
            code: "INVALID_STATUS_FILTER",
            message: `알 수 없는 status: ${statusRaw} (가능: ${ProjectStatusSchema.options.join(", ")})`,
          },
        },
        { status: 422 },
      );
    }

    let result: Project[] = [...DEMO_PROJECTS];
    if (statusRaw !== null) result = result.filter((p) => p.status === statusRaw);
    if (q) {
      result = result.filter(
        (p) => p.title.toLowerCase().includes(q) || p.topic.toLowerCase().includes(q),
      );
    }
    result.sort((a, b) => b.created_at.localeCompare(a.created_at));
    const items = result.slice(offset, offset + limit);

    return HttpResponse.json({ data: items }, { status: 200 });
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
      status: "created",
      depth_mode: v.depth_mode,
      owner_id: DEMO_ADMIN_USER.id,
      owner_name: DEMO_ADMIN_USER.name,
      created_at: nowIso,
      updated_at: nowIso,
      progress: 0,
    };
    DEMO_PROJECTS.unshift(project);
    createProjectFolderForLibrary(project);
    return HttpResponse.json({ data: project }, { status: 201 });
  }),

  // DELETE /projects/{id} — 완료·보관 상태만 허용(그 외 422)
  http.delete(url("projects/:id"), ({ params }) => {
    const id = String(params.id);
    const idx = DEMO_PROJECTS.findIndex((p) => p.id === id);
    if (idx < 0) {
      return HttpResponse.json(
        { error: { code: "PROJECT_NOT_FOUND", message: "프로젝트를 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    const status = DEMO_PROJECTS[idx].status;
    if (status !== "completed" && status !== "archived") {
      return HttpResponse.json(
        {
          error: {
            code: "PROJECT_NOT_DELETABLE",
            message: `완료된 프로젝트만 삭제할 수 있습니다(현재: ${status})`,
          },
        },
        { status: 422 },
      );
    }
    DEMO_PROJECTS.splice(idx, 1);
    return new HttpResponse(null, { status: 204 });
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
    if (project.status !== "created") {
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

  // GET /projects/{id}/verify-report — PM 검증 경고 리포트(실계약 미러, 데모 2건)
  http.get(url("projects/:id/verify-report"), ({ params }) => {
    const id = String(params.id);
    return HttpResponse.json(
      {
        data: [
          {
            id: `${id}-vf-1`,
            chapter_number: 2,
            severity: "critical",
            category: "법령 시점",
            section_ref: "2.3",
            detail:
              "「고령친화산업진흥법」이 2장에서는 '시행 중', 4장에서는 '개정 추진 중'으로 상충 표기됨",
            created_at: "2026-07-24T02:00:00Z",
          },
          {
            id: `${id}-vf-2`,
            chapter_number: 3,
            severity: "warning",
            category: "통계 중복",
            section_ref: "3.1",
            detail: "고령화율 24.1% 통계가 1.2절과 동일 출처로 중복 인용됨 — 한쪽은 요약 처리 권장",
            created_at: "2026-07-24T02:00:00Z",
          },
        ],
      },
      { status: 200 },
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
