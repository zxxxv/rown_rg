import { Navigate, useParams } from "react-router-dom";

/** 본문 검토(QA)는 보고서 화면으로 통합됐다(2026-08-07) - 기존 /qa-select 링크·
 * 북마크는 /preview로 넘긴다. 검토·블록 편집·승인이 모두 그 화면에서 이뤄진다. */
export default function QaSelectPage() {
  const { id: projectId = "" } = useParams<{ id: string }>();
  return <Navigate to={`/projects/${projectId}/preview`} replace />;
}
