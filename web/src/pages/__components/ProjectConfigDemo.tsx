import { toast } from "sonner";
import { ProjectConfigForm } from "@/features/project-config/ProjectConfigForm";
import { PRESET_DEFAULTS } from "@/features/project-config/presets";
import type { ProjectFormValues } from "@/features/project-config/schema";

const EDIT_DEFAULT: ProjectFormValues = {
  title: "광역교통망 확충 사업 예비타당성조사",
  topic: "수도권 광역교통망의 5년간 추진 현황을 점검하고 잔여 노선의 추진 우선순위를 재정렬한다.",
  deadline: "2026-08-15",
  config: PRESET_DEFAULTS.preliminary_feasibility,
};

export function ProjectConfigCreateDemo() {
  return (
    <ProjectConfigForm
      mode="create"
      onSubmit={(v) => {
        toast.success("create.submit (mock)", {
          description: `${v.title || "(제목 없음)"} · ${v.config.depth_mode}`,
        });
      }}
      onCancel={() => toast("create.cancel")}
    />
  );
}

export function ProjectConfigEditDemo() {
  return (
    <ProjectConfigForm
      mode="edit"
      defaultValues={EDIT_DEFAULT}
      onSubmit={(v) => {
        toast.success("edit.submit (mock)", {
          description: `${v.config.enabled_analyzers.length}개 분석 도구 적용`,
        });
      }}
      onCancel={() => toast("edit.cancel")}
    />
  );
}
