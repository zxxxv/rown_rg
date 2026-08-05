import { DEMO_PROJECTS } from "@/api/mock/fixtures/projects";
import {
  PERSONAL_PROMPTS,
  personalPromptNode,
  SYSTEM_PROMPTS,
  systemPromptNode,
} from "@/api/mock/fixtures/prompts";
import { DEMO_ADMIN_USER } from "@/api/mock/fixtures/users";
import type { LibraryFileMeta, LibraryNode, Project, SourceKind, UserRoleType } from "@/api/types";

interface FileSpec {
  name: string;
  size_bytes: number;
  days_ago: number;
  registered_by: string;
  source_kind: SourceKind;
  page_count?: number;
  visible_to_roles?: UserRoleType[];
  project_id?: string;
}

const NOW = new Date("2026-05-27T00:00:00Z");
let idCounter = 0;
const nextId = (prefix: string) => `${prefix}_${String(++idCounter).padStart(3, "0")}`;

function offsetIso(days: number): string {
  const d = new Date(NOW);
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString();
}

function file(spec: FileSpec): LibraryNode {
  const meta: LibraryFileMeta = {
    size_bytes: spec.size_bytes,
    registered_at: offsetIso(spec.days_ago),
    registered_by: spec.registered_by,
    source_kind: spec.source_kind,
    visible_to_roles: spec.visible_to_roles ?? ["viewer", "worker", "admin", "super_admin"],
    ...(spec.page_count !== undefined ? { page_count: spec.page_count } : {}),
    ...(spec.project_id !== undefined ? { project_id: spec.project_id } : {}),
  };
  return { id: nextId("file"), name: spec.name, type: "file", file_meta: meta };
}

function folder(name: string, children: LibraryNode[]): LibraryNode {
  return { id: nextId("dir"), name, type: "folder", children };
}

/** 가상 파일로 표시(프로젝트 소스는 읽기전용 — 삭제/권한변경 불가). */
function asVirtualFile(n: LibraryNode): LibraryNode {
  return n.type === "file" ? { ...n, virtual: true } : n;
}

/** AI 수집 자료 — 가상 파일 + 수집 원문 인라인 뷰어 경로(content_url). */
function asAiVirtualFile(n: LibraryNode): LibraryNode {
  return n.type === "file"
    ? { ...n, virtual: true, content_url: `library/sources/${n.id}/content` }
    : n;
}

function projectFolder(project: Project, children: LibraryNode[] = []): LibraryNode {
  const base = `pf_${project.id}`;
  return {
    id: base,
    name: project.title,
    type: "folder",
    virtual: true,
    children: [
      {
        id: `${base}_ai`,
        name: "AI 수집 자료",
        type: "folder",
        virtual: true,
        children: children.filter(isAiKind).map(asAiVirtualFile),
      },
      {
        id: `${base}_up`,
        name: "사용자 업로드",
        type: "folder",
        virtual: true,
        children: children.filter(isUploadKind).map(asVirtualFile),
      },
      {
        id: `${base}_lib`,
        name: "라이브러리 참조",
        type: "folder",
        virtual: true,
        children: children.filter(isLibraryKind).map(asVirtualFile),
      },
    ],
  };
}

function isAiKind(n: LibraryNode): boolean {
  return (
    n.type === "file" &&
    (n.file_meta.source_kind === "gov" ||
      n.file_meta.source_kind === "academic" ||
      n.file_meta.source_kind === "media")
  );
}
function isUploadKind(n: LibraryNode): boolean {
  return n.type === "file" && n.file_meta.source_kind === "upload";
}
function isLibraryKind(n: LibraryNode): boolean {
  return n.type === "file" && n.file_meta.source_kind === "library";
}

const SHARED_CHILDREN: LibraryNode[] = [
  folder("클라이언트A", [
    folder("2024-Q1 사업", [
      file({
        name: "사업개요_v3.pdf",
        size_bytes: 320_000,
        days_ago: 41,
        registered_by: "최재웅",
        source_kind: "library",
        page_count: 12,
      }),
      file({
        name: "시장분석_2024.hwpx",
        size_bytes: 1_800_000,
        days_ago: 38,
        registered_by: "박지영",
        source_kind: "library",
        page_count: 48,
      }),
      file({
        name: "재무현황_분기보고.xlsx",
        size_bytes: 95_000,
        days_ago: 28,
        registered_by: "최재웅",
        source_kind: "library",
      }),
    ]),
    folder("2024-Q2 사업", [
      file({
        name: "사업제안서_초안.docx",
        size_bytes: 540_000,
        days_ago: 20,
        registered_by: "박지영",
        source_kind: "library",
        page_count: 24,
      }),
      file({
        name: "시장조사_결과.xlsx",
        size_bytes: 210_000,
        days_ago: 15,
        registered_by: "정현우",
        source_kind: "library",
      }),
    ]),
  ]),

  folder("클라이언트B", [
    folder("신규 검토", [
      file({
        name: "사업제안서.pdf",
        size_bytes: 880_000,
        days_ago: 5,
        registered_by: "박지영",
        source_kind: "library",
        page_count: 32,
        visible_to_roles: ["worker", "admin", "super_admin"],
      }),
      file({
        name: "기술명세서.pdf",
        size_bytes: 1_200_000,
        days_ago: 3,
        registered_by: "정현우",
        source_kind: "library",
        page_count: 56,
      }),
    ]),
  ]),

  folder("공통자료", [
    folder("정부 통계", [
      file({
        name: "통계청_인구.xlsx",
        size_bytes: 8_200_000,
        days_ago: 60,
        registered_by: "system",
        source_kind: "gov",
      }),
      file({
        name: "통계청_경제활동.xlsx",
        size_bytes: 12_400_000,
        days_ago: 55,
        registered_by: "system",
        source_kind: "gov",
      }),
      file({
        name: "KOSIS_2024_3분기.xlsx",
        size_bytes: 4_700_000,
        days_ago: 12,
        registered_by: "system",
        source_kind: "gov",
      }),
    ]),
    folder("정부 보고서", [
      file({
        name: "KDI_경제전망_2024.pdf",
        size_bytes: 3_100_000,
        days_ago: 22,
        registered_by: "최재웅",
        source_kind: "gov",
        page_count: 138,
      }),
      file({
        name: "한국은행_금융안정보고서.pdf",
        size_bytes: 2_800_000,
        days_ago: 40,
        registered_by: "system",
        source_kind: "gov",
        page_count: 96,
      }),
      file({
        name: "기재부_재정전략_2024.pdf",
        size_bytes: 1_900_000,
        days_ago: 35,
        registered_by: "system",
        source_kind: "gov",
        page_count: 78,
      }),
    ]),
    folder("법률·규제", [
      file({
        name: "국가재정법.pdf",
        size_bytes: 420_000,
        days_ago: 180,
        registered_by: "system",
        source_kind: "gov",
        page_count: 88,
      }),
      file({
        name: "예비타당성조사_운용지침.pdf",
        size_bytes: 540_000,
        days_ago: 80,
        registered_by: "최재웅",
        source_kind: "gov",
        page_count: 42,
      }),
    ]),
    folder("학술 논문", [
      file({
        name: "SOC_경제효과_분석.pdf",
        size_bytes: 2_200_000,
        days_ago: 95,
        registered_by: "박지영",
        source_kind: "academic",
        page_count: 56,
      }),
      file({
        name: "인구_고령화_재정영향.pdf",
        size_bytes: 1_700_000,
        days_ago: 110,
        registered_by: "윤서연",
        source_kind: "academic",
        page_count: 64,
      }),
      file({
        name: "스마트시티_사례연구.pdf",
        size_bytes: 2_500_000,
        days_ago: 70,
        registered_by: "강민지",
        source_kind: "academic",
        page_count: 72,
      }),
    ]),
    folder("언론·보도", [
      file({
        name: "조선일보_GTX_특집.pdf",
        size_bytes: 680_000,
        days_ago: 18,
        registered_by: "정현우",
        source_kind: "media",
        page_count: 8,
      }),
      file({
        name: "한겨레_스마트시티_기획.pdf",
        size_bytes: 420_000,
        days_ago: 25,
        registered_by: "강민지",
        source_kind: "media",
        page_count: 6,
      }),
    ]),
  ]),

  folder("분석 자료", [
    folder("STEEP 분석", [
      file({
        name: "STEEP_2024_표준양식.pdf",
        size_bytes: 380_000,
        days_ago: 130,
        registered_by: "최재웅",
        source_kind: "library",
        page_count: 18,
      }),
      file({
        name: "STEEP_적용사례.docx",
        size_bytes: 220_000,
        days_ago: 90,
        registered_by: "박지영",
        source_kind: "library",
      }),
    ]),
    folder("SWOT 분석", [
      file({
        name: "SWOT_사례모음.pdf",
        size_bytes: 480_000,
        days_ago: 75,
        registered_by: "박지영",
        source_kind: "library",
        page_count: 32,
      }),
    ]),
    folder("비용편익 모델", [
      file({
        name: "BC_표준양식.xlsx",
        size_bytes: 180_000,
        days_ago: 200,
        registered_by: "최재웅",
        source_kind: "library",
      }),
      file({
        name: "BC_민감도분석_가이드.pdf",
        size_bytes: 720_000,
        days_ago: 150,
        registered_by: "정현우",
        source_kind: "library",
        page_count: 36,
      }),
    ]),
  ]),
];

const W4_DEMO_SAMPLE_FILES: LibraryNode[] = [
  file({
    name: "국토부_광역교통_2030_계획.pdf",
    size_bytes: 2_400_000,
    days_ago: 6,
    registered_by: "AI 수집",
    source_kind: "gov",
    page_count: 96,
    project_id: "proj_demo_w4",
  }),
  file({
    name: "KDI_SOC_사업_분석_사례.pdf",
    size_bytes: 1_800_000,
    days_ago: 6,
    registered_by: "AI 수집",
    source_kind: "academic",
    page_count: 64,
    project_id: "proj_demo_w4",
  }),
  file({
    name: "조선일보_GTX_연장_특집.pdf",
    size_bytes: 320_000,
    days_ago: 5,
    registered_by: "AI 수집",
    source_kind: "media",
    page_count: 6,
    project_id: "proj_demo_w4",
  }),
  file({
    name: "현장조사_OO지역.docx",
    size_bytes: 480_000,
    days_ago: 3,
    registered_by: "최재웅",
    source_kind: "upload",
    page_count: 18,
    project_id: "proj_demo_w4",
  }),
  file({
    name: "예비타당성조사_운용지침.pdf",
    size_bytes: 540_000,
    days_ago: 4,
    registered_by: "최재웅",
    source_kind: "library",
    page_count: 42,
    project_id: "proj_demo_w4",
  }),
];

function sampleFilesForProject(p: Project): LibraryNode[] {
  if (p.id === "proj_demo_w4") return W4_DEMO_SAMPLE_FILES;
  if (p.status === "created") return [];
  const seed = p.id.length;
  const items: LibraryNode[] = [
    file({
      name: "AI수집_정부보고서.pdf",
      size_bytes: 1_200_000 + seed * 1000,
      days_ago: 4,
      registered_by: "AI 수집",
      source_kind: "gov",
      page_count: 48,
      project_id: p.id,
    }),
    file({
      name: "AI수집_학술논문.pdf",
      size_bytes: 980_000 + seed * 1000,
      days_ago: 4,
      registered_by: "AI 수집",
      source_kind: "academic",
      page_count: 32,
      project_id: p.id,
    }),
  ];
  if (seed % 2 === 0) {
    items.push(
      file({
        name: "현장자료.docx",
        size_bytes: 320_000,
        days_ago: 2,
        registered_by: "박지영",
        source_kind: "upload",
        project_id: p.id,
      }),
    );
  }
  return items;
}

/** 실 폴더에 writable(업로드·폴더생성 대상) 컨텍스트를 재귀로 부여. */
function withWritable(nodes: LibraryNode[], scope: "personal" | "company"): LibraryNode[] {
  return nodes.map((n) =>
    n.type === "folder"
      ? { ...n, writable: { parent_id: n.id, scope }, children: withWritable(n.children, scope) }
      : n,
  );
}

// 백엔드 get_tree와 동일한 2탑레벨: 개인 루트(나만) + 회사 공유(조직 전체).
export const LIBRARY_TREE: LibraryNode[] = [
  {
    id: "me",
    name: DEMO_ADMIN_USER.name,
    type: "folder",
    virtual: true,
    children: [
      {
        id: "me-projects",
        name: "프로젝트",
        type: "folder",
        virtual: true,
        children: DEMO_PROJECTS.map((p) => projectFolder(p, sampleFilesForProject(p))),
      },
      {
        id: "me-prompts",
        name: "프롬프트",
        type: "folder",
        virtual: true,
        children: [
          myPromptFolder("me-agents", "내 에이전트", "agent"),
          myPromptFolder("me-rules", "내 작성 규칙", "rule"),
        ],
      },
      {
        id: "me-files",
        name: "내 자료",
        type: "folder",
        virtual: true,
        writable: { parent_id: null, scope: "personal" },
        children: [],
      },
    ],
  },
  {
    id: "company",
    name: "회사 공유",
    type: "folder",
    virtual: true,
    writable: { parent_id: null, scope: "company" },
    children: [...withWritable(SHARED_CHILDREN, "company"), systemPromptsFolder()],
  },
];

function myPromptFolder(id: string, name: string, kind: "agent" | "rule"): LibraryNode {
  return {
    id,
    name,
    type: "folder",
    virtual: true,
    children: PERSONAL_PROMPTS.filter((p) => p.kind === kind).map(personalPromptNode),
  };
}

function systemPromptsFolder(): LibraryNode {
  return {
    id: "sys-prompts",
    name: "시스템 프롬프트",
    type: "folder",
    virtual: true,
    children: [
      {
        id: "sys-agents",
        name: "에이전트",
        type: "folder",
        virtual: true,
        children: SYSTEM_PROMPTS.filter((p) => p.kind === "agent").map(systemPromptNode),
      },
      {
        id: "sys-rules",
        name: "작성 규칙",
        type: "folder",
        virtual: true,
        children: SYSTEM_PROMPTS.filter((p) => p.kind === "rule").map(systemPromptNode),
      },
    ],
  };
}

/** 개인 프롬프트 CRUD 후 트리의 '내 에이전트/내 작성 규칙'을 스토어에서 다시 채운다. */
export function syncPromptFolders(): void {
  const agents = findFolderById(LIBRARY_TREE, "me-agents");
  if (agents) {
    agents.children = PERSONAL_PROMPTS.filter((p) => p.kind === "agent").map(personalPromptNode);
  }
  const rules = findFolderById(LIBRARY_TREE, "me-rules");
  if (rules) {
    rules.children = PERSONAL_PROMPTS.filter((p) => p.kind === "rule").map(personalPromptNode);
  }
}

export function createProjectFolderForLibrary(project: Project): void {
  const group = findFolderById(LIBRARY_TREE, "me-projects");
  if (!group) return;
  if (group.children.some((n) => n.id === `pf_${project.id}`)) return;
  group.children.unshift(projectFolder(project));
}

export function getLibraryFilesForProject(projectId: string): LibraryNode[] {
  const group = findFolderById(LIBRARY_TREE, "me-projects");
  if (!group) return [];
  const pf = group.children.find((n) => n.id === `pf_${projectId}`);
  if (!pf || pf.type !== "folder") return [];
  const result: LibraryNode[] = [];
  for (const sub of pf.children) {
    if (sub.type === "folder") result.push(...sub.children);
  }
  return result;
}

function findFolderById(
  nodes: LibraryNode[],
  id: string,
): Extract<LibraryNode, { type: "folder" }> | null {
  for (const n of nodes) {
    if (n.type !== "folder") continue;
    if (n.id === id) return n;
    const sub = findFolderById(n.children, id);
    if (sub) return sub;
  }
  return null;
}
