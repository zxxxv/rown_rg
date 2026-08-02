import { ArrowLeft, ArrowRight, CheckCircle2, Download, FileCode, FileText } from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { useProject } from "@/api/projects";
import type { Project } from "@/api/types";
import { EmptyState } from "@/components/feedback/EmptyState";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { env } from "@/env";
import { useDownload } from "@/features/export/useDownload";
import { VerifyReportCard } from "@/features/export/VerifyReportCard";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";

const COMPANY_STYLE: { label: string; value: string }[] = [
  { label: "본문 폰트", value: "함초롬바탕 11pt" },
  { label: "제목", value: "함초롬돋움 16/14/12pt" },
  { label: "줄간격", value: "160%" },
  { label: "여백", value: "상하 20mm · 좌 30mm · 우 20mm" },
  { label: "머리말", value: "주식회사 로운인사이트" },
  { label: "꼬리말", value: "페이지 번호" },
];

export default function ExportPage() {
  const { id: projectId = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { user, logout } = useAuth();

  const projectQuery = useProject(projectId);
  const project = projectQuery.data;

  return (
    <AppShell
      user={user ? { name: user.name, role: user.role } : null}
      onLogout={() => void logout()}
      tokenUsage={{ used: 1_240_000, limit: 5_000_000 }}
    >
      <div className="flex flex-col gap-6">
        <Button
          variant="ghost"
          size="sm"
          className="w-fit text-fg-secondary"
          onClick={() => navigate(`/projects/${projectId}/overview`)}
        >
          <ArrowLeft className="mr-1 h-4 w-4" />
          프로젝트 개요
        </Button>

        {projectQuery.isLoading ? (
          <LoadingSkeleton variant="block" />
        ) : projectQuery.isError || !project ? (
          <EmptyState
            title="프로젝트를 찾을 수 없습니다"
            action={
              <Button variant="outline" onClick={() => navigate("/projects")}>
                목록으로
              </Button>
            }
          />
        ) : project.status !== "completed" ? (
          <NotReadyView project={project} onNavigate={navigate} />
        ) : (
          <CompletedView project={project} />
        )}
      </div>
    </AppShell>
  );
}

function NotReadyView({
  project,
  onNavigate,
}: {
  project: Project;
  onNavigate: (to: string) => void;
}) {
  return (
    <EmptyState
      title="아직 완료되지 않은 보고서입니다"
      description={`현재 상태: ${project.status} — 작성·검증이 끝난 후 출력할 수 있습니다.`}
      action={
        <Button onClick={() => onNavigate(`/projects/${project.id}/progress`)}>
          진행 패널로 <ArrowRight className="ml-1 h-4 w-4" />
        </Button>
      }
    />
  );
}

function CompletedView({ project }: { project: Project }) {
  const download = useDownload();
  const pages = 280;
  const components = 142;
  const completedAt = project.updated_at?.slice(0, 10) ?? project.created_at.slice(0, 10);

  return (
    <>
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <h1 className="text-3xl font-semibold text-fg">보고서 출력</h1>
            <Badge variant="default" className="bg-bg-success text-fg-success">
              <CheckCircle2 className="mr-1 h-3.5 w-3.5" />
              진행률 100%
            </Badge>
          </div>
          <p className="text-sm text-fg-secondary">{project.title}</p>
          <dl className="flex flex-wrap gap-x-4 font-mono text-xs text-fg-tertiary">
            <Meta label="전체 페이지" value={`~${pages}p`} />
            <Meta label="컴포넌트" value={`${components}개`} />
            <Meta label="작성 완료" value={completedAt} />
          </dl>
        </div>
      </header>

      <VerifyReportCard projectId={project.id} />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <FormatCard
          icon={FileText}
          title="HWPX"
          subtitle="회사 표준 양식"
          size="~2.8 MB"
          accent
          thumbnailLabel="HWPX 미리보기"
          showStyle
          onDownload={() =>
            download({
              // 파일 응답 — ky 언래핑 없이 브라우저 다운로드로 직접 연결한다.
              url: `${env.VITE_API_BASE_URL.replace(/\/$/, "")}/projects/${project.id}/export`,
              filename: `${project.title}.hwpx`,
              label: "HWPX",
            })
          }
        />
        <FormatCard
          icon={FileCode}
          title="Markdown"
          subtitle="기술 검토용 원본"
          size="~180 KB"
          thumbnailLabel="MD 원문"
          onDownload={() =>
            download({
              url: "/samples/sample.md",
              filename: `${project.id}.md`,
              label: "Markdown",
            })
          }
        />
      </div>

      <Separator />

      <section className="flex flex-col gap-3">
        <header>
          <h2 className="text-base font-semibold text-fg">부가 자료</h2>
          <p className="text-xs text-fg-tertiary">검수·재현·검증용 보조 출력물</p>
        </header>
        <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <ExtraRow
            label="출처 색인 (CSV)"
            description="본문 내 모든 [ref:id] → 자료 매핑"
            onDownload={() =>
              download({
                url: "/samples/sample-refs.csv",
                filename: `${project.id}-refs.csv`,
                label: "출처 색인",
              })
            }
          />
          <ExtraRow
            label="편집 이력 (JSON)"
            description="편집기 본격 작동 후 활성"
            disabledReason="편집 이력 없음 — 추후 활성화"
          />
          <ExtraRow
            label="일관성 그래프 (JSON)"
            description="섹션 간 주장·수치 그래프"
            disabledReason="추후 활성화"
          />
          <ExtraRow
            label="약어·용어집 (HWPX 부록)"
            description="자동 추출된 용어 정의"
            disabledReason="추후 활성화"
          />
        </ul>
      </section>
    </>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-1">
      <dt>{label}</dt>
      <dd className="text-fg-secondary">{value}</dd>
    </div>
  );
}

function FormatCard({
  icon: Icon,
  title,
  subtitle,
  size,
  thumbnailLabel,
  showStyle,
  accent,
  onDownload,
}: {
  icon: typeof FileText;
  title: string;
  subtitle: string;
  size: string;
  thumbnailLabel: string;
  showStyle?: boolean;
  accent?: boolean;
  onDownload: () => void;
}) {
  return (
    <Card className={cn(accent && "border-accent/40 bg-bg-info")}>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-lg">{title}</CardTitle>
          <Icon className="h-5 w-5 text-fg-secondary" aria-hidden />
        </div>
        <CardDescription>{subtitle}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div
          role="img"
          aria-label={thumbnailLabel}
          className="flex aspect-[3/4] items-center justify-center rounded border border-border bg-bg-secondary text-xs text-fg-tertiary"
        >
          {thumbnailLabel}
        </div>
        {showStyle ? (
          <dl className="flex flex-col gap-1 rounded border border-border bg-bg p-2 font-mono text-[11px]">
            {COMPANY_STYLE.map((row) => (
              <div key={row.label} className="flex justify-between gap-2">
                <dt className="text-fg-tertiary">{row.label}</dt>
                <dd className="text-right text-fg">{row.value}</dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="text-xs text-fg-tertiary">원본 마크다운 — 양식 없이 그대로 출력합니다.</p>
        )}
        <div className="flex items-center justify-between gap-2">
          <span className="font-mono text-xs text-fg-tertiary">{size}</span>
          <Button size="sm" onClick={onDownload}>
            <Download className="mr-1 h-3.5 w-3.5" />
            다운로드
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function ExtraRow({
  label,
  description,
  onDownload,
  disabledReason,
}: {
  label: string;
  description: string;
  onDownload?: () => void;
  disabledReason?: string;
}) {
  const disabled = Boolean(disabledReason);
  return (
    <li
      className={cn(
        "flex items-center justify-between gap-3 rounded border border-border bg-bg p-3",
        disabled && "opacity-60",
      )}
    >
      <div className="flex flex-col gap-0.5">
        <span className="text-sm font-medium text-fg">{label}</span>
        <span className="text-xs text-fg-tertiary">{disabledReason ?? description}</span>
      </div>
      <Button
        size="sm"
        variant={disabled ? "ghost" : "outline"}
        disabled={disabled}
        onClick={onDownload}
      >
        <Download className="h-3.5 w-3.5" />
      </Button>
    </li>
  );
}
