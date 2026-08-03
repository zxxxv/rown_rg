import { z } from "zod";

export const UserRoleSchema = z.enum(["viewer", "worker", "admin", "super_admin"]);
export type UserRoleType = z.infer<typeof UserRoleSchema>;

export const UserSchema = z.object({
  id: z.string(),
  email: z.string().email(),
  // 이메일 대신 로그인에 쓸 수 있는 아이디(없는 계정도 있음)
  username: z.string().nullish(),
  name: z.string(),
  role: UserRoleSchema,
  // 프로필(마이페이지)용 — /auth/me 만 채워줌. 로그인 응답 등에선 생략될 수 있어 optional.
  is_active: z.boolean().optional(),
  has_password: z.boolean().optional(),
  last_login_at: z.string().nullish(),
  password_changed_at: z.string().nullish(),
  created_at: z.string().optional(),
});
export type User = z.infer<typeof UserSchema>;

// 로컬 기본값 카탈로그(features/project-config/presets.ts)용 레거시 키.
// 실제 프리셋 카탈로그는 백엔드 GET /presets 가 단일 진실이며 id/name 문자열을 쓴다.
export const PresetSchema = z.enum([
  "preliminary_feasibility",
  "business_review",
  "policy_research",
  "blank",
]);
export type Preset = z.infer<typeof PresetSchema>;

export const DepthModeSchema = z.enum(["outline_only", "standard", "full_report", "deep_dive"]);
export type DepthMode = z.infer<typeof DepthModeSchema>;

// 백엔드 ProjectStage(core/types.py)와 동일한 어휘 — failed 없음, 검토 대기는 reviewing.
export const ProjectStatusSchema = z.enum([
  "created",
  "researching",
  "indexing",
  "writing",
  "reviewing",
  "completed",
  "archived",
]);
export type ProjectStatus = z.infer<typeof ProjectStatusSchema>;

export const AnalyzerSchema = z.enum([
  "STEEP",
  "SWOT",
  "FIVE_FORCES",
  "PESTLE",
  "RISK",
  "COST_BENEFIT",
]);
export type Analyzer = z.infer<typeof AnalyzerSchema>;

// 사용자 확정 목차(config.outline) — 있으면 백엔드 planner가 LLM 없이 그대로 실행한다.
// 장·절 번호는 배열 위치에서 파생되므로 클라이언트는 번호를 보내지 않는다.
// 주의: .default()를 쓰면 zod input/output 타입이 갈라져 zodResolver와 어긋난다 —
// 전 필드 필수(서버는 항상 전 필드 직렬화, 편집기도 항상 채움).
export const OutlineSectionSchema = z.object({
  title: z.string(),
  direction: z.string(),
  key_points: z.array(z.string()),
  // 분석 에이전트 name 참조 — 첫 번째가 대표(페르소나·분량 기준)
  analysts: z.array(z.string()),
});
export type OutlineSection = z.infer<typeof OutlineSectionSchema>;

export const OutlineChapterSchema = z.object({
  title: z.string(),
  sections: z.array(OutlineSectionSchema),
});
export type OutlineChapter = z.infer<typeof OutlineChapterSchema>;

export const OutlineSchema = z.object({
  chapters: z.array(OutlineChapterSchema),
});
export type Outline = z.infer<typeof OutlineSchema>;

export const ProjectConfigSchema = z.object({
  // 백엔드 계약: 프리셋은 카탈로그 id/name 문자열 또는 null(자유 주제)
  preset: z.string().nullable(),
  // 생성 화면에서 직접 확정한 목차 — 없으면(AI 자동 설계) 필드 자체를 생략한다.
  outline: OutlineSchema.optional(),
  sources: z.object({
    use_library: z.boolean(),
    use_upload: z.boolean(),
    use_web_search: z.boolean(),
  }),
  enabled_analyzers: z.array(AnalyzerSchema),
  enable_pre_reconciliation: z.boolean(),
  enable_consistency_graph: z.boolean(),
  enable_dual_track_search: z.boolean(),
  enable_source_tagging: z.boolean(),
  enable_critic_agent: z.boolean(),
  enable_glossary: z.boolean(),
  depth_mode: DepthModeSchema,
  output_formats: z.array(z.enum(["hwpx", "markdown"])),
  notification_channels: z.array(z.enum(["email", "naver_works"])),
});
export type ProjectConfig = z.infer<typeof ProjectConfigSchema>;

// 백엔드 config는 dict[str, Any] — 빈/부분 config가 와도 UI가 죽지 않게 기본값으로 흡수한다.
export const DEFAULT_PROJECT_CONFIG: ProjectConfig = {
  preset: null,
  sources: { use_library: true, use_upload: true, use_web_search: true },
  enabled_analyzers: [],
  enable_pre_reconciliation: false,
  enable_consistency_graph: false,
  enable_dual_track_search: false,
  enable_source_tagging: false,
  enable_critic_agent: false,
  enable_glossary: false,
  depth_mode: "full_report",
  output_formats: ["hwpx"],
  notification_channels: [],
};

// 백엔드 ProjectRead(schemas/project.py)와 1:1 — progress만 프론트 전용 초과 필드(optional).
export const ProjectSchema = z.object({
  id: z.string(),
  title: z.string(),
  topic: z.string(),
  preset: z.string().nullable(),
  config: ProjectConfigSchema.catch(DEFAULT_PROJECT_CONFIG),
  status: ProjectStatusSchema,
  depth_mode: DepthModeSchema,
  owner_id: z.string(),
  // 표시용 소유자 이름(owner eager-load 시에만 채워짐) — 없으면 owner_id로 폴백
  owner_name: z.string().nullish(),
  created_at: z.string(),
  updated_at: z.string(),
  progress: z.number().min(0).max(100).optional(),
});
export type Project = z.infer<typeof ProjectSchema>;

// 실백엔드 GET /projects — 페이지네이션 봉투 없이 배열을 반환한다(limit/offset 쿼리).
export const ProjectListSchema = z.array(ProjectSchema);

export const ProjectSortSchema = z.enum(["created_desc", "title_asc"]);
export type ProjectSort = z.infer<typeof ProjectSortSchema>;

export const SourceKindSchema = z.enum([
  "gov",
  "academic",
  "media",
  "library",
  "upload",
  "web_search",
]);
export type SourceKind = z.infer<typeof SourceKindSchema>;

export const SourceSchema = z.object({
  id: z.string(),
  project_id: z.string(),
  title: z.string(),
  source: z.string(),
  source_kind: SourceKindSchema,
  url: z.string().optional(),
  published_at: z.string().optional(),
  pages: z.number().int().nonnegative().optional(),
  reliability: z.number().min(0).max(1),
  summary: z.string(),
  is_included: z.boolean().nullable(),
  quotes: z.array(z.string()).optional(),
  preview: z.string().optional(),
  /** 이 출처가 매칭된 목차 절 제목들(수집 LLM 판정) — 검토 화면 '관련 목차' 표시용 */
  matched_sections: z.array(z.string()).optional(),
  library_file_id: z.string().optional(),
});
export type Source = z.infer<typeof SourceSchema>;

export const SourceListResponseSchema = z.object({
  items: z.array(SourceSchema),
  total: z.number().int().nonnegative(),
});
export type SourceListResponse = z.infer<typeof SourceListResponseSchema>;

export const ContradictionWinnerSchema = z.enum(["A", "B", "defer"]);
export type ContradictionWinner = z.infer<typeof ContradictionWinnerSchema>;

export const ContradictionSourceSchema = z.object({
  source_id: z.string(),
  title: z.string(),
  source: z.string(),
  source_kind: SourceKindSchema,
  published_at: z.string(),
  organization: z.string(),
  page_number: z.number().int().nonnegative().optional(),
  reliability: z.number().min(0).max(1),
  quote: z.string(),
});
export type ContradictionSource = z.infer<typeof ContradictionSourceSchema>;

export const ContradictionResolutionSchema = z.object({
  winner: ContradictionWinnerSchema,
  resolved_at: z.string(),
  resolved_by: z.string(),
});
export type ContradictionResolution = z.infer<typeof ContradictionResolutionSchema>;

export const ContradictionSchema = z.object({
  id: z.string(),
  project_id: z.string(),
  subject: z.string(),
  value_a: z.string(),
  value_b: z.string(),
  source_a: ContradictionSourceSchema,
  source_b: ContradictionSourceSchema,
  status: z.enum(["pending", "resolved"]),
  resolution: ContradictionResolutionSchema.optional(),
  created_at: z.string(),
});
export type Contradiction = z.infer<typeof ContradictionSchema>;

export const ContradictionListResponseSchema = z.object({
  items: z.array(ContradictionSchema),
});
export type ContradictionListResponse = z.infer<typeof ContradictionListResponseSchema>;

export const SectionStatusSchema = z.enum(["pending", "writing", "completed", "failed"]);
export type SectionStatus = z.infer<typeof SectionStatusSchema>;

export const SectionNodeSchema = z.object({
  id: z.string(),
  title: z.string(),
  level: z.number().int().min(1).max(4),
  status: SectionStatusSchema,
  parent_id: z.string(),
});
export type SectionNode = z.infer<typeof SectionNodeSchema>;

export const ChapterNodeSchema = z.object({
  id: z.string(),
  title: z.string(),
  level: z.literal(1),
  status: SectionStatusSchema,
  children: z.array(SectionNodeSchema),
});
export type ChapterNode = z.infer<typeof ChapterNodeSchema>;

export const SectionTreeResponseSchema = z.object({
  tree: z.array(ChapterNodeSchema),
});
export type SectionTreeResponse = z.infer<typeof SectionTreeResponseSchema>;

export const SectionContentResponseSchema = z.object({
  id: z.string(),
  title: z.string(),
  content: z.string(),
  source_ids: z.array(z.string()),
  qa_status: z.enum(["passed", "failed", "pending"]),
  level: z.number().int().min(1).max(4),
});
export type SectionContentResponse = z.infer<typeof SectionContentResponseSchema>;

export const LibraryFileMetaSchema = z.object({
  size_bytes: z.number().int().nonnegative(),
  registered_at: z.string(),
  registered_by: z.string(),
  source_kind: SourceKindSchema,
  page_count: z.number().int().nonnegative().optional(),
  visible_to_roles: z.array(UserRoleSchema),
  project_id: z.string().optional(),
});
export type LibraryFileMeta = z.infer<typeof LibraryFileMetaSchema>;

// 업로드·폴더생성 대상 컨텍스트. parent_id=null은 최상위(개인/회사 공유 루트 바로 아래).
// writable이 없으면 쓰기 불가(가상 컨테이너: 프로젝트/프롬프트 등).
export const WritableTargetSchema = z.object({
  parent_id: z.string().nullish(),
  scope: z.enum(["personal", "company"]),
});
export type WritableTarget = z.infer<typeof WritableTargetSchema>;

// 프롬프트 파일 노드 마커 — 있으면 상세 패널이 프롬프트 에디터/뷰어를 연다.
export const PromptRefSchema = z.object({
  scope: z.enum(["personal", "system"]),
  kind: z.enum(["agent", "rule"]),
  ref: z.string(),
  editable: z.boolean(),
});
export type PromptRef = z.infer<typeof PromptRefSchema>;

// virtual: 합성 노드(개인 루트·프로젝트·완성본·소스 등) — 삭제/권한변경 불가.
// download_url: 가상 파일의 다운로드 경로(API base 상대경로 또는 절대 URL). 실파일은 없음.
export type LibraryNode =
  | {
      id: string;
      name: string;
      type: "folder";
      children: LibraryNode[];
      virtual?: boolean;
      writable?: WritableTarget | null;
    }
  | {
      id: string;
      name: string;
      type: "file";
      file_meta: LibraryFileMeta;
      virtual?: boolean;
      download_url?: string | null;
      prompt?: PromptRef | null;
    };

export const LibraryNodeSchema: z.ZodType<LibraryNode> = z.lazy(() =>
  z.union([
    z.object({
      id: z.string(),
      name: z.string(),
      type: z.literal("folder"),
      children: z.array(LibraryNodeSchema),
      virtual: z.boolean().optional(),
      writable: WritableTargetSchema.nullish(),
    }),
    z.object({
      id: z.string(),
      name: z.string(),
      type: z.literal("file"),
      file_meta: LibraryFileMetaSchema,
      virtual: z.boolean().optional(),
      download_url: z.string().nullish(),
      prompt: PromptRefSchema.nullish(),
    }),
  ]),
);

export const LibraryTreeResponseSchema = z.object({
  tree: z.array(LibraryNodeSchema),
});
export type LibraryTreeResponse = z.infer<typeof LibraryTreeResponseSchema>;

export const LoginInputSchema = z.object({
  login_id: z.string().min(1, "이메일 또는 아이디를 입력하세요"),
  password: z.string().min(8, "비밀번호는 8자 이상이어야 합니다"),
});
export type LoginInput = z.infer<typeof LoginInputSchema>;

export const LoginResponseSchema = z.object({
  user: UserSchema,
});
export type LoginResponse = z.infer<typeof LoginResponseSchema>;

export const MeResponseSchema = UserSchema;
export type MeResponse = z.infer<typeof MeResponseSchema>;

// 마이페이지 — 내 토큰 사용량 (cost_usd 는 백엔드가 Decimal→문자열로 줄 수 있어 coerce)
export const TokenUsageDailyPointSchema = z.object({
  date: z.string(),
  input_tokens: z.number().int().nonnegative(),
  output_tokens: z.number().int().nonnegative(),
  cost_usd: z.coerce.number().nonnegative(),
});
export type TokenUsageDailyPoint = z.infer<typeof TokenUsageDailyPointSchema>;

export const TokenUsageByModelSchema = z.object({
  model: z.string(),
  input_tokens: z.number().int().nonnegative(),
  output_tokens: z.number().int().nonnegative(),
  cost_usd: z.coerce.number().nonnegative(),
  request_count: z.number().int().nonnegative(),
});
export type TokenUsageByModel = z.infer<typeof TokenUsageByModelSchema>;

export const MyTokenUsageSchema = z.object({
  period_start: z.string(),
  period_end: z.string(),
  total_input_tokens: z.number().int().nonnegative(),
  total_output_tokens: z.number().int().nonnegative(),
  total_cost_usd: z.coerce.number().nonnegative(),
  request_count: z.number().int().nonnegative(),
  daily: z.array(TokenUsageDailyPointSchema),
  by_model: z.array(TokenUsageByModelSchema),
});
export type MyTokenUsage = z.infer<typeof MyTokenUsageSchema>;

// 마이페이지 — 비밀번호 변경
export const ChangePasswordInputSchema = z
  .object({
    current_password: z.string().min(1, "현재 비밀번호를 입력하세요"),
    new_password: z.string().min(8, "새 비밀번호는 8자 이상이어야 합니다"),
    confirm_password: z.string().min(1, "새 비밀번호를 한 번 더 입력하세요"),
  })
  .refine((v) => v.new_password === v.confirm_password, {
    message: "새 비밀번호가 일치하지 않습니다",
    path: ["confirm_password"],
  })
  .refine((v) => v.new_password !== v.current_password, {
    message: "현재 비밀번호와 다른 비밀번호를 사용하세요",
    path: ["new_password"],
  });
export type ChangePasswordInput = z.infer<typeof ChangePasswordInputSchema>;
