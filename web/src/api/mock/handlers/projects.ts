import { HttpResponse, http } from "msw";
import { z } from "zod";
import { createProjectFolderForLibrary } from "@/api/mock/fixtures/library";
import { DEMO_PROJECTS } from "@/api/mock/fixtures/projects";
import { DRIFT_ROWS, SECTION_LOCKS } from "@/api/mock/fixtures/section-state";
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

    // 'in_progress'는 단계값이 아니라 그룹 필터(created~reviewing) - 백엔드와 동일.
    const inProgressStatuses = ["created", "researching", "indexing", "writing", "reviewing"];
    if (
      statusRaw !== null &&
      statusRaw !== "in_progress" &&
      !ProjectStatusSchema.safeParse(statusRaw).success
    ) {
      return HttpResponse.json(
        {
          error: {
            code: "INVALID_STATUS_FILTER",
            message: `알 수 없는 status: ${statusRaw} (가능: in_progress, ${ProjectStatusSchema.options.join(", ")})`,
          },
        },
        { status: 422 },
      );
    }

    let result: Project[] = [...DEMO_PROJECTS];
    if (statusRaw === "in_progress") {
      // 조립까지 끝났어도 확정 전이면 진행 중이다 - 아직 손보는 문서다(백엔드와 같은 규칙).
      result = result.filter(
        (p) =>
          inProgressStatuses.includes(p.status) || (p.status === "completed" && !p.finalized_at),
      );
    } else if (statusRaw === "completed") {
      // 완료 = 최종 확정된 것만. 파이프라인 완주는 사이클이 끝난 것일 뿐이다.
      result = result.filter((p) => p.status === "completed" && Boolean(p.finalized_at));
    } else if (statusRaw !== null) {
      result = result.filter((p) => p.status === statusRaw);
    }
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

  // DELETE /projects/{id} - 완료·보관 상태만 허용(그 외 422)
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

  // POST /projects/{id}/run - 백그라운드 실행 시작(202)
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

  // POST /projects/{id}/reopen - 완료 보고서를 다시 연다.
  // 단계는 정하지 않고 **되짚는다**: 본문이 있으니 reviewing이다. 예전엔 researching을
  // 박아 넣어 자료 화면이 "AI가 검색 중"으로 읽었다(2026-08-26 파생화).
  http.post(url("projects/:id/reopen"), ({ params }) => {
    const project = DEMO_PROJECTS.find((p) => p.id === String(params.id));
    if (!project) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "프로젝트를 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    if (project.status !== "completed") {
      return HttpResponse.json(
        {
          error: {
            code: "PROJECT_NOT_REOPENABLE",
            message: "완료된 보고서만 다시 열 수 있습니다",
          },
        },
        { status: 422 },
      );
    }
    project.status = "reviewing";
    project.finalized_at = null;
    project.updated_at = new Date().toISOString();
    return HttpResponse.json({ data: project }, { status: 200 });
  }),

  // POST /projects/{id}/cancel - 진행 중 실행 취소(협조적). 목에선 즉시 cancelled.
  http.post(url("projects/:id/cancel"), ({ params }) => {
    const id = String(params.id);
    const project = DEMO_PROJECTS.find((p) => p.id === id);
    if (!project) {
      return HttpResponse.json(
        { error: { code: "PROJECT_NOT_FOUND", message: "프로젝트를 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    if (["completed", "archived", "cancelled"].includes(project.status)) {
      return HttpResponse.json(
        {
          error: {
            code: "PROJECT_NOT_CANCELLABLE",
            message: `이미 종료된 프로젝트입니다(현재: ${project.status})`,
          },
        },
        { status: 422 },
      );
    }
    if (project.status === "created") {
      return HttpResponse.json(
        { error: { code: "PROJECT_NOT_RUNNING", message: "아직 시작하지 않은 프로젝트입니다" } },
        { status: 422 },
      );
    }
    project.status = "cancelled";
    project.updated_at = new Date().toISOString();
    return HttpResponse.json(
      { data: { project_id: project.id, status: "cancelled" } },
      { status: 202 },
    );
  }),

  // GET /projects/{id}/verify-report - PM 검증 경고 리포트(실계약 미러, 데모 2건)
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
            category: "수치 일관성",
            section_ref: "3.1",
            detail:
              "고령화율이 1.2절은 24.1%, 3.1절은 24.6%로 상이하게 인용됨 - 출처 기준 연도 확인 필요",
            created_at: "2026-07-24T02:00:00Z",
          },
        ],
      },
      { status: 200 },
    );
  }),

  // GET /projects/{id}/verify-report/status - 재검증 진행 여부(데모는 항상 idle)
  http.get(url("projects/:id/verify-report/status"), () => {
    return HttpResponse.json({ data: { running: false } }, { status: 200 });
  }),

  // POST /projects/{id}/verify-report - 재검증 시작(데모는 즉시 수락만)
  http.post(url("projects/:id/verify-report"), () => {
    return HttpResponse.json({ data: { started: true } }, { status: 202 });
  }),

  // POST /projects/{id}/collect-more - 자료 더 모으기(게이트 무관, 데모는 수락만)
  http.post(url("projects/:id/collect-more"), () => {
    return HttpResponse.json(
      { data: { started: true, running: true, reopen_gate: false } },
      { status: 202 },
    );
  }),

  // GET /projects/{id}/rewrite-batch - 묶음 재작성 진행(데모는 항상 idle)
  http.get(url("projects/:id/rewrite-batch"), () => {
    return HttpResponse.json(
      {
        data: { running: false, total: 0, done: 0, current: "", failures: {}, cancelled: false },
      },
      { status: 200 },
    );
  }),

  // DELETE /projects/{id}/rewrite-batch - 묶음 멈춤(데모는 즉시 수락만)
  http.delete(url("projects/:id/rewrite-batch"), () => {
    return HttpResponse.json({ data: { cancelled: true } }, { status: 200 });
  }),

  // POST /projects/{id}/rewrite-batch - 고른 절 다시 쓰기 시작(데모는 즉시 수락만)
  http.post(url("projects/:id/rewrite-batch"), () => {
    return HttpResponse.json({ data: { started: true, running: true, total: 1 } }, { status: 202 });
  }),

  // 미반영 무시("이대로 두기") - 서버는 지금 계획의 지문을 찍어 표시만 지운다.
  // 데모에서도 누른 절이 목록에서 사라져야 버튼이 아무 일도 안 하는 것처럼 안 보인다.
  http.post(url("projects/:id/drift/dismiss"), async ({ request }) => {
    const body = (await request.json()) as { section_ids?: string[] };
    const ids = body.section_ids ?? [];
    const labels: string[] = [];
    for (const id of ids) {
      const row = DRIFT_ROWS.get(id);
      if (!row) continue;
      DRIFT_ROWS.delete(id);
      labels.push(row.label);
    }
    return HttpResponse.json({ data: { dismissed: labels, skipped: [] } }, { status: 200 });
  }),

  // GET /projects/{id}/cost-basis - 절당 비용 실측(이 보고서 자기 것).
  // 값이 프로젝트마다 3배 넘게 벌어져서 일반 단가를 못 쓴다 - 목도 그 사실을 흉내낸다.
  http.get(url("projects/:id/cost-basis"), () => {
    return HttpResponse.json(
      {
        data: {
          per_section_usd: 0.702,
          n_sections_measured: 20,
          basis: "project",
          spent_usd: 14.05,
        },
      },
      { status: 200 },
    );
  }),

  // GET /projects/{id}/sources/{sid}/impact - 이 자료를 빼면 무엇이 무너지나.
  // 자료 id로 갈라 준다: 걸린 절이 없어 확인창 없이 그냥 빠지는 경로도 눌러 봐야 한다.
  http.get(url("projects/:id/sources/:sid/impact"), ({ params }) => {
    const sid = String(params.sid);
    if (sid.endsWith("001")) {
      // 가장 아픈 경우 - 유일한 근거인 절이 섞여 있다.
      return HttpResponse.json(
        {
          data: {
            n_sections: 2,
            n_citations: 5,
            n_sole: 1,
            sections: [
              {
                section_id: "2.3",
                label: "2.3 인구·고령화 영향",
                n_citations: 3,
                sole: true,
                locked: false,
              },
              {
                section_id: "3.3",
                label: "3.3 비용편익비 (B/C)",
                n_citations: 2,
                sole: false,
                locked: false,
              },
            ],
          },
        },
        { status: 200 },
      );
    }
    if (sid.endsWith("002")) {
      return HttpResponse.json(
        {
          data: {
            n_sections: 1,
            n_citations: 2,
            n_sole: 0,
            sections: [
              {
                section_id: "3.3",
                label: "3.3 비용편익비 (B/C)",
                n_citations: 2,
                sole: false,
                locked: false,
              },
            ],
          },
        },
        { status: 200 },
      );
    }
    return HttpResponse.json(
      { data: { n_sections: 0, n_citations: 0, n_sole: 0, sections: [] } },
      { status: 200 },
    );
  }),

  // GET /projects/{id}/drift - 미반영 절(설계를 고쳤는데 본문이 아직 안 담은 절)
  http.get(url("projects/:id/drift"), () => {
    const sections = [...DRIFT_ROWS.values()].map((row) => ({
      ...row,
      locked: SECTION_LOCKS.get(row.section_id) ?? false,
    }));
    return HttpResponse.json(
      {
        data: {
          sections,
          n_plan_changed: sections.filter((s) => s.reasons.includes("plan_changed")).length,
          n_source_excluded: sections.filter((s) => s.reasons.includes("source_excluded")).length,
          n_missing: sections.filter((s) => s.reasons.includes("missing")).length,
        },
      },
      { status: 200 },
    );
  }),

  // POST /projects/{id}/finalize - 최종 확정. DELETE는 확정 해제.
  // 핸들러가 없으면 실백엔드로 새고 그 401이 전역 로그아웃을 때린다(2026-08-25 실사고).
  // '완료 = 확정' 규칙을 세우면서 이 버튼이 전면에 나왔으므로 목에도 반드시 있어야 한다.
  http.post(url("projects/:id/finalize"), ({ params }) => {
    const project = DEMO_PROJECTS.find((p) => p.id === String(params.id));
    if (!project) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "프로젝트를 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    if (project.status !== "completed") {
      return HttpResponse.json(
        {
          error: {
            code: "PROJECT_NOT_FINALIZABLE",
            message: "완료된 보고서만 확정할 수 있습니다",
          },
        },
        { status: 422 },
      );
    }
    project.finalized_at = new Date().toISOString();
    project.updated_at = project.finalized_at;
    return HttpResponse.json({ data: project }, { status: 200 });
  }),

  http.delete(url("projects/:id/finalize"), ({ params }) => {
    const project = DEMO_PROJECTS.find((p) => p.id === String(params.id));
    if (!project) {
      return HttpResponse.json(
        { error: { code: "not_found", message: "프로젝트를 찾을 수 없습니다." } },
        { status: 404 },
      );
    }
    project.finalized_at = null;
    project.updated_at = new Date().toISOString();
    return HttpResponse.json({ data: project }, { status: 200 });
  }),

  // POST /projects/{id}/versions - 수동 버전 저장("이 상태를 남겨 두고 계속 고친다").
  http.post(url("projects/:id/versions"), () => {
    return HttpResponse.json({ data: { version_no: 7, created: true } }, { status: 200 });
  }),

  // POST /projects/{id}/versions/{n}/restore/{sec} - 그 절만 그때로 되돌리기.
  http.post(url("projects/:id/versions/:no/restore/:sec"), ({ params }) => {
    return HttpResponse.json(
      { data: { restored: true, section_id: String(params.sec), version_no: Number(params.no) } },
      { status: 200 },
    );
  }),

  // GET /projects/{id}/versions - 보고서 버전 스냅샷(실계약 미러).
  // 핸들러가 없으면 onUnhandledRequest:"bypass"로 실백엔드까지 새고, 그 401이
  // 전역 로그아웃 처리기를 때려 완료 프로젝트 개요가 통째로 /login으로 튕긴다
  // (2026-08-25 CDP 관통에서 발견 - 8/21 버전 기능 추가 때 빠진 구멍).
  http.get(url("projects/:id/versions"), () => {
    return HttpResponse.json(
      {
        // 사유는 열린 형태다 - 이정표(assemble·finalize·manual·restore)와 절 단위
        // 자동 스냅샷(edit·rewrite·block)이 섞여 내려온다. 기록 카드가 둘을 갈라
        // 접는 게 실계약이라, 목킹도 섞어 둬야 그 화면을 눌러 볼 수 있다(2026-08-27).
        data: [
          {
            version_no: 6,
            reason: "manual:부장님 검토 전",
            created_at: new Date(Date.now() - 1_800_000).toISOString(),
            n_sections: 20,
            total_chars: 202_140,
          },
          {
            version_no: 5,
            reason: "restore:3.2",
            created_at: new Date(Date.now() - 2_400_000).toISOString(),
            n_sections: 20,
            total_chars: 201_902,
          },
          {
            version_no: 4,
            reason: "edit:3.2",
            created_at: new Date(Date.now() - 2_700_000).toISOString(),
            n_sections: 20,
            total_chars: 202_310,
          },
          {
            version_no: 3,
            reason: "rewrite:2.1",
            created_at: new Date(Date.now() - 3_000_000).toISOString(),
            n_sections: 20,
            total_chars: 201_774,
          },
          {
            version_no: 2,
            reason: "finalize",
            created_at: new Date(Date.now() - 3_600_000).toISOString(),
            n_sections: 20,
            total_chars: 201_558,
          },
          {
            version_no: 1,
            reason: "assemble",
            created_at: new Date(Date.now() - 86_400_000).toISOString(),
            n_sections: 20,
            total_chars: 196_204,
          },
        ],
      },
      { status: 200 },
    );
  }),

  // GET /projects/{id}/versions/diff - 절 단위 비교(데모는 변화 없음)
  http.get(url("projects/:id/versions/diff"), ({ request }) => {
    const base = Number(new URL(request.url).searchParams.get("base") ?? 1);
    return HttpResponse.json(
      {
        data: {
          base_version: base,
          target_version: null,
          n_added: 0,
          n_removed: 0,
          n_modified: 0,
          n_unchanged: 20,
          entries: [],
        },
      },
      { status: 200 },
    );
  }),

  // GET /projects/{id}/insights - 시사점 2~3쪽 요약(웹 전용, HWPX 미포함)
  http.get(url("projects/:id/insights"), () => {
    return HttpResponse.json(
      {
        data: {
          content: [
            "## 핵심 요약",
            "",
            "□ EU CBAM 전환기간이 2026년 종료되면서 국내 수출기업의 실질 부담이 시작된다",
            "  ㅇ 철강·알루미늄·시멘트 3개 품목이 대EU 수출액의 12.4%를 차지",
            "  ㅇ 전환기간 중 보고 의무만 졌던 기업들이 2026년부터 인증서 구매 의무를 진다",
            "",
            "□ 국내 배출권 가격과 EU ETS 가격 격차가 비용으로 전가된다",
            "  ㅇ 국내 K-ETS 평균 8,700원 대 EU ETS 약 91유로 - 격차만큼 CBAM 인증서를 사야 한다",
            "",
            "## 주요 시사점",
            "",
            "□ 배출량 산정 체계가 준비된 기업과 아닌 기업의 격차가 벌어진다",
            "  ㅇ 실측 기반 산정으로 전환한 기업은 기본값 적용 대비 부담이 30% 이상 낮아진다",
            "",
            "□ 중소 협력사의 데이터 부재가 대기업 부담으로 되돌아온다",
            "  ㅇ 공급망 배출량을 못 대면 EU 기본값이 적용되어 원청이 비용을 떠안는다",
            "",
            "## 제언",
            "",
            "□ (산업부, 2026년 상반기) 중소 협력사 배출량 산정 지원 사업을 신설한다",
            "  ㅇ 대상은 CBAM 6개 품목 공급망 내 종업원 300인 미만 사업장",
            "",
            "□ (기업, 즉시) 실측 기반 배출량 산정 체계로 전환한다",
          ].join("\n"),
          source_sections: ["2.5 환경분석 종합 및 시사점", "6.2 핵심 시사점 및 제언"],
          model: "claude-sonnet-4-6",
          running: false,
        },
      },
      { status: 200 },
    );
  }),

  // POST /projects/{id}/insights - 요약 다시 만들기(데모는 즉시 수락만)
  http.post(url("projects/:id/insights"), () => {
    return HttpResponse.json({ data: { started: true, running: true } }, { status: 202 });
  }),

  // GET /projects/{id}/verify-coverage - 검사 커버리지(실계약 미러)
  http.get(url("projects/:id/verify-coverage"), () => {
    return HttpResponse.json(
      {
        data: {
          n_sections: 35,
          n_candidates: 412,
          n_claims: 388,
          claim_coverage: 0.94,
          missed_numeric: 0,
          llm_verify_enabled: true,
          pm_verify_enabled: true,
        },
      },
      { status: 200 },
    );
  }),

  // GET /projects/{id}/export - HWPX 파일(더미 blob)
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
