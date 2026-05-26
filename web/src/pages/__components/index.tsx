import { FileText, FolderOpen, Inbox } from "lucide-react";
import { toast } from "sonner";
import { ConfidenceBadge } from "@/components/data-display/ConfidenceBadge";
import { CostEstimate } from "@/components/data-display/CostEstimate";
import { ReviewCheckpoint } from "@/components/data-display/ReviewCheckpoint";
import { SourceCard } from "@/components/data-display/SourceCard";
import { StatusDot } from "@/components/data-display/StatusDot";
import { EmptyState } from "@/components/feedback/EmptyState";
import { ErrorBoundary } from "@/components/feedback/ErrorBoundary";
import { LoadingSkeleton } from "@/components/feedback/LoadingSkeleton";
import { AppShell } from "@/components/layout/AppShell";
import { KbdHint } from "@/components/primitives/KbdHint";
import { Button } from "@/components/ui/button";
import { AuthDemo } from "@/pages/__components/AuthDemo";
import {
  ProjectConfigCreateDemo,
  ProjectConfigEditDemo,
} from "@/pages/__components/ProjectConfigDemo";
import { WebSocketDemo } from "@/pages/__components/WebSocketDemo";

export default function ComponentsGalleryPage() {
  const mockUser = { name: "최재웅", role: "admin" as const };

  return (
    <AppShell
      user={mockUser}
      tokenUsage={{ used: 1_240_000, limit: 5_000_000 }}
      onLogout={() => toast("로그아웃 (mock)")}
    >
      <div className="flex flex-col gap-10">
        <header>
          <h1 className="text-3xl font-semibold text-fg">컴포넌트 갤러리</h1>
          <p className="mt-1 text-sm text-fg-secondary">
            §8 공통 컴포넌트의 시각 확인용 페이지 — DEV 빌드에서만 노출됩니다.
          </p>
        </header>

        <Section title="1. AppShell">
          <p className="text-sm text-fg-secondary">
            현재 페이지의 사이드바·헤더가 곧 AppShell입니다. 좌측 토글 버튼으로 접힘/펼침을
            확인하세요.
          </p>
        </Section>

        <Section title="2. RequireAuth + useAuth">
          <p className="text-sm text-fg-secondary">
            라우팅 가드 컴포넌트 자체는 시각 요소가 없습니다. 아래는 <code>useAuth</code> 훅의 실제
            동작 데모 — MSW가 <code>/auth/*</code>를 가로채 응답합니다.
          </p>
          <AuthDemo />
        </Section>

        <Section title="3. ReviewCheckpoint">
          <ReviewCheckpoint
            number={1}
            title="자료 검토"
            description="AI가 수집한 자료 중 어떤 것을 사용할지 결정해 주세요."
            decisions={[
              { label: "승인", intent: "primary", onClick: () => toast("승인") },
              { label: "수정 요청", intent: "secondary", onClick: () => toast("수정 요청") },
              { label: "여기서 멈춤", intent: "danger", onClick: () => toast("중단") },
            ]}
          >
            <p className="text-sm text-fg-secondary">검토 대상 내용이 여기에 표시됩니다.</p>
          </ReviewCheckpoint>
        </Section>

        <Section title="4. CostEstimate">
          <CostEstimate estimatedHours={5.5} estimatedTokens={2_100_000} estimatedCostUsd={130} />
        </Section>

        <Section title="5. StatusDot">
          <div className="flex flex-wrap items-center gap-6">
            <StatusDot kind="success" label="통과" />
            <StatusDot kind="info" label="진행 중" />
            <StatusDot kind="warning" label="경고" />
            <StatusDot kind="danger" label="실패" />
            <StatusDot kind="tertiary" label="대기" />
          </div>
        </Section>

        <Section title="6. ConfidenceBadge">
          <div className="flex flex-wrap items-center gap-3">
            <ConfidenceBadge value={0.42} />
            <ConfidenceBadge value={0.68} />
            <ConfidenceBadge value={0.95} />
          </div>
        </Section>

        <Section title="7. SourceCard (3가지 actions 변형)">
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <SourceCard
              title="2024 인구 고령화율 추이 보고서"
              source="통계청"
              publishedAt="2024-08-12"
              pages={42}
              reliability={0.94}
              summary="통계청이 발간한 2024년 인구 동향 분석. 65세 이상 인구 비율의 지역별 격차와 5년 단위 추이를 다룬다."
              onClick={() => toast("자료 상세")}
              actions={
                <>
                  <Button size="sm">채택</Button>
                  <Button size="sm" variant="secondary">
                    제외
                  </Button>
                </>
              }
            />
            <SourceCard
              title="대도시권 광역교통망 계획안"
              source="국토교통부"
              publishedAt="2023-11-30"
              pages={156}
              reliability={0.71}
              summary="GTX·BRT를 포함한 광역교통망의 중장기 계획. 노선·예산·예상 일정 등을 포함."
              onClick={() => toast("자료 상세")}
              actions={
                <Button size="sm" variant="outline">
                  원본 열기
                </Button>
              }
            />
            <SourceCard
              title="민간 부동산 시장 분석 (블로그)"
              source="example.blog.com"
              publishedAt="2025-02-04"
              pages={3}
              reliability={0.38}
              summary="개인 블로그의 시장 분석. 출처 신뢰도 낮음."
              actions={
                <>
                  <Button size="sm" variant="destructive">
                    삭제
                  </Button>
                  <Button size="sm" variant="ghost">
                    상세
                  </Button>
                </>
              }
            />
          </div>
        </Section>

        <Section title="8. KbdHint">
          <div className="flex flex-wrap items-center gap-3 text-sm text-fg-secondary">
            <span>편집 모드 토글</span>
            <KbdHint keys={["E"]} />
            <span>저장</span>
            <KbdHint keys={["Cmd", "S"]} />
            <span>전체 검색</span>
            <KbdHint keys={["Ctrl", "K"]} />
          </div>
        </Section>

        <Section title="9. EmptyState">
          <EmptyState
            icon={Inbox}
            title="아직 자료가 없습니다"
            description="파일을 업로드하거나 회사 라이브러리에서 추가해 주세요."
            action={<Button>자료 추가</Button>}
          />
        </Section>

        <Section title="10. LoadingSkeleton">
          <div className="flex flex-col gap-6">
            <div>
              <p className="mb-2 text-xs text-fg-tertiary">variant = card</p>
              <LoadingSkeleton variant="card" count={3} />
            </div>
            <div>
              <p className="mb-2 text-xs text-fg-tertiary">variant = row</p>
              <LoadingSkeleton variant="row" count={4} />
            </div>
          </div>
        </Section>

        <Section title="11. ErrorBoundary">
          <ErrorBoundary>
            <BoomDemo />
          </ErrorBoundary>
        </Section>

        <Section title="12. ProjectConfigForm — mode=create (영역 1~7 + sticky 견적 사이드바)">
          <ProjectConfigCreateDemo />
        </Section>

        <Section title="13. ProjectConfigForm — mode=edit (제목·주제·작성 깊이 readonly + 프리셋 잠금)">
          <ProjectConfigEditDemo />
        </Section>

        <Section title="14. WebSocket 디버그 패널 (시나리오 picker + 재연결)">
          <WebSocketDemo />
        </Section>

        <Section title="기타 — Lucide 아이콘 토큰 확인">
          <div className="flex items-center gap-4 text-fg-secondary">
            <FolderOpen className="h-5 w-5" />
            <FileText className="h-5 w-5" />
            <span className="text-xs">아이콘은 현재 텍스트 색을 따릅니다.</span>
          </div>
        </Section>
      </div>
    </AppShell>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-xl font-semibold text-fg">{title}</h2>
      {children}
    </section>
  );
}

function BoomDemo() {
  return (
    <Button
      variant="destructive"
      onClick={() => {
        throw new Error("의도적 에러 — ErrorBoundary 동작 확인");
      }}
    >
      에러 던지기
    </Button>
  );
}
