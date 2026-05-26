import { z } from "zod";

export const PhaseNameSchema = z.enum(["research", "indexing", "writing", "qa", "export"]);
export type PhaseName = z.infer<typeof PhaseNameSchema>;

export const PhaseStatusSchema = z.enum(["started", "completed"]);
export type PhaseStatus = z.infer<typeof PhaseStatusSchema>;

export const StreamChannelSchema = z.enum([
  "critic_thinking",
  "research_keywords",
  "contradiction_explain",
]);
export type StreamChannel = z.infer<typeof StreamChannelSchema>;

export const ProgressMessageSchema = z.discriminatedUnion("type", [
  z.object({
    type: z.literal("phase"),
    phase: PhaseNameSchema,
    status: PhaseStatusSchema,
  }),
  z.object({
    type: z.literal("step"),
    phase: z.string(),
    step: z.string(),
    status: z.enum(["started", "completed", "failed"]),
    eta_seconds: z.number().int().nonnegative().optional(),
  }),
  z.object({
    type: z.literal("stream"),
    channel: StreamChannelSchema,
    delta: z.string(),
  }),
  z.object({
    type: z.literal("fake_stream"),
    section_id: z.string(),
    delta: z.string(),
  }),
  z.object({
    type: z.literal("cost"),
    tokens_used: z.number().nonnegative(),
    cost_usd: z.number().nonnegative(),
  }),
  z.object({
    type: z.literal("checkpoint"),
    checkpoint_id: z.string(),
    level: z.union([z.literal(1), z.literal(2)]),
    requires_user_decision: z.literal(true),
  }),
  z.object({
    type: z.literal("error"),
    code: z.string(),
    message: z.string(),
  }),
]);
export type ProgressMessage = z.infer<typeof ProgressMessageSchema>;
