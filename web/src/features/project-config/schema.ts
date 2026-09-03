import { z } from "zod";
import { ProjectConfigSchema } from "@/api/types";
import { LIMITS } from "./validation";

export const ProjectFormSchema = z.object({
  title: z
    .string()
    .min(1, "보고서 제목을 입력하세요")
    .max(LIMITS.title, `제목은 ${LIMITS.title}자 이내여야 합니다`),
  topic: z
    .string()
    .min(1, "주제를 입력하세요")
    .max(LIMITS.topic, `주제는 ${LIMITS.topic}자 이내여야 합니다`),
  config: ProjectConfigSchema,
});
export type ProjectFormValues = z.infer<typeof ProjectFormSchema>;
