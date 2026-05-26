import type { LibraryFileMeta, LibraryNode, SourceKind, UserRoleType } from "@/api/types";

interface FileSpec {
  name: string;
  size_bytes: number;
  days_ago: number;
  registered_by: string;
  source_kind: SourceKind;
  page_count?: number;
  visible_to_roles?: UserRoleType[];
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
  };
  return { id: nextId("file"), name: spec.name, type: "file", file_meta: meta };
}

function folder(name: string, children: LibraryNode[]): LibraryNode {
  return { id: nextId("dir"), name, type: "folder", children };
}

export const LIBRARY_TREE: LibraryNode[] = [
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
