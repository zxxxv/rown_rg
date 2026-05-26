import { z } from "zod";

export const UserRoleSchema = z.enum(["viewer", "worker", "admin", "super_admin"]);
export type UserRoleType = z.infer<typeof UserRoleSchema>;

export const UserSchema = z.object({
  id: z.string(),
  email: z.string().email(),
  name: z.string(),
  role: UserRoleSchema,
});
export type User = z.infer<typeof UserSchema>;

export const PresetSchema = z.enum([
  "preliminary_feasibility",
  "business_review",
  "policy_research",
  "blank",
]);
export type Preset = z.infer<typeof PresetSchema>;

export const DepthModeSchema = z.enum(["outline_only", "standard", "full_report", "deep_dive"]);
export type DepthMode = z.infer<typeof DepthModeSchema>;

export const ProjectStatusSchema = z.enum([
  "draft",
  "researching",
  "indexing",
  "writing",
  "qa",
  "review",
  "completed",
  "archived",
  "failed",
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

export const ProjectConfigSchema = z.object({
  preset: PresetSchema,
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
  output_formats: z.array(z.enum(["hwpx", "pdf", "markdown"])),
  notification_channels: z.array(z.enum(["email", "naver_works"])),
});
export type ProjectConfig = z.infer<typeof ProjectConfigSchema>;

export const ProjectSchema = z.object({
  id: z.string(),
  title: z.string(),
  topic: z.string(),
  preset: PresetSchema,
  config: ProjectConfigSchema,
  status: ProjectStatusSchema,
  depth_mode: DepthModeSchema,
  owner_id: z.string(),
  created_at: z.string(),
  updated_at: z.string().optional(),
  deadline: z.string().optional(),
  progress: z.number().min(0).max(100).optional(),
});
export type Project = z.infer<typeof ProjectSchema>;

export const ProjectListResponseSchema = z.object({
  items: z.array(ProjectSchema),
  total: z.number().int().nonnegative(),
});
export type ProjectListResponse = z.infer<typeof ProjectListResponseSchema>;

export const ProjectSortSchema = z.enum(["created_desc", "deadline_asc", "title_asc"]);
export type ProjectSort = z.infer<typeof ProjectSortSchema>;

export const SourceKindSchema = z.enum(["gov", "academic", "media", "library", "upload"]);
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
});
export type LibraryFileMeta = z.infer<typeof LibraryFileMetaSchema>;

export type LibraryNode =
  | { id: string; name: string; type: "folder"; children: LibraryNode[] }
  | { id: string; name: string; type: "file"; file_meta: LibraryFileMeta };

export const LibraryNodeSchema: z.ZodType<LibraryNode> = z.lazy(() =>
  z.union([
    z.object({
      id: z.string(),
      name: z.string(),
      type: z.literal("folder"),
      children: z.array(LibraryNodeSchema),
    }),
    z.object({
      id: z.string(),
      name: z.string(),
      type: z.literal("file"),
      file_meta: LibraryFileMetaSchema,
    }),
  ]),
);

export const LibraryTreeResponseSchema = z.object({
  tree: z.array(LibraryNodeSchema),
});
export type LibraryTreeResponse = z.infer<typeof LibraryTreeResponseSchema>;

export const LoginInputSchema = z.object({
  id: z.string().min(1, "사번 또는 이메일을 입력하세요"),
  password: z.string().min(8, "비밀번호는 8자 이상이어야 합니다"),
});
export type LoginInput = z.infer<typeof LoginInputSchema>;

export const LoginResponseSchema = z.object({
  user: UserSchema,
});
export type LoginResponse = z.infer<typeof LoginResponseSchema>;

export const MeResponseSchema = UserSchema;
export type MeResponse = z.infer<typeof MeResponseSchema>;
