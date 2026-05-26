import { z } from "zod";
import { ProjectConfigSchema } from "@/api/types";

export const ProjectFormSchema = z.object({
  title: z.string().min(1, "보고서 제목을 입력하세요").max(255, "제목은 255자 이내여야 합니다"),
  topic: z.string().min(1, "주제를 입력하세요").max(2000, "주제는 2000자 이내여야 합니다"),
  deadline: z.string().optional(),
  config: ProjectConfigSchema,
});
export type ProjectFormValues = z.infer<typeof ProjectFormSchema>;
