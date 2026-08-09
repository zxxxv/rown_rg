# rown-rg — 프론트엔드

AI 보고서 자동생성 시스템의 웹 UI. React + Vite + TypeScript.
백엔드(`../src/`)와 모노레포 구조. 같은 도메인 통합 전제로 Vite proxy가 `/api` → `localhost:8000` 전달.

---

## 빠른 시작

```bash
# Node 20 LTS 권장 (.nvmrc 참고)
pnpm install
pnpm dev
# → http://localhost:5173
```

시연용 로그인 (이메일 또는 아이디 모두 가능):
- ID: `admin_rown` (또는 `admin@loweninsight.kr`)
- PW: `Admin_rown12!`

발표 시연 모드: URL에 `?demo=1` 추가 → 가속 시나리오 + 시연용 데이터.

---

## 명령어

| 명령 | 용도 |
|---|---|
| `pnpm dev` | 개발 서버 (5173) |
| `pnpm build` | 프로덕션 빌드 |
| `pnpm preview` | 빌드 산출물 미리보기 |
| `pnpm typecheck` | TypeScript 타입 검사 |
| `pnpm lint` | Biome 린트 |
| `pnpm format` | Biome 포맷 |
| `pnpm preflight` | 발표 직전 12종 자동 점검 |

---

## 화면 현황

### 작동하는 화면

| 라우트 | 화면 | 비고 |
|---|---|---|
| `/login` | 로그인 | 5회 실패 423 처리 |
| `/projects` | 프로젝트 목록 | 50건 더미, URL 필터 동기화 |
| `/projects/new` | 새 프로젝트 (7개 영역) | 4종 프리셋 + 견적 실시간 |
| `/projects/:id/overview` | 프로젝트 허브 | 옵션 변경 mode="edit" |
| `/projects/:id/sources` | 자료 검토 | Optimistic UI |
| `/projects/:id/progress` | 진행 패널 | WebSocket 시나리오 재생 |
| `/projects/:id/preview` | 섹션 미리보기 | [ref:id] 출처 호버 |

### 모킹 화면 (클릭·전환 작동, 백엔드 실작동은)

| 라우트 | 화면 | 실작동 시점 |
|---|---|---|
| `/projects/:id/reconcile` | 자료 모순 해결 | (세션 23) |
| `/projects/:id/editor` | 3-패널 편집기 셸 | (세션 29, Tiptap) |
| `/projects/:id/export` | HWPX 다운로드 | (세션 28) |
| `/admin/dashboard` | 관리자 대시보드 | (세션 35) |
| `/library` | 자료 폴더 라이브러리 | (세션 24, react-arborist) |

### 권한 페이지

| 라우트 | 비고 |
|---|---|
| `/403` | 권한 없음 안내 |

---

## 아직 안 만든 것

- Section Brief 입력 UI
- 검토 지점 결정 흐름 본격 현재 모킹)
- 다단계 QA 결과 표시
- 일관성 그래프 시각화 — react-flow
- 부분 섹션 재작성 실작동
- 자료 폴더 라이브러리 실작동 — react-arborist
- LLM-as-Judge 평가 UI
- 네이버 웍스 알림
- IP 통제·임시 허용
- HWPX 양식 관리자 설정
- Tiptap 본격 작동
- 비교 뷰
- 약어·용어집 자동 생성
- 분석 에이전트 STEEP·SWOT
- 이미지 자리표시자
- 토큰 한도 대시보드 실작동
- Critic 토큰 스트리밍 본격
- 페이크 스트리밍 Level 4 본문
- 네이버 웍스 SSO

---

## 아키텍처

### 기술 스택

- **빌드**: Vite 5 + React 18.3 + TypeScript 5 (strict)
- **상태**: TanStack Query v5 (서버) + useState/Context (로컬)
- **라우팅**: React Router v6 data router
- **스타일**: Tailwind CSS v3 + shadcn/ui (CSS 변수 토큰)
- **폼**: react-hook-form + zod
- **통신**: ky (HTTP) + 네이티브 WebSocket
- **목**: MSW v2 (개발 환경만 활성)
- **에디터**: Tiptap
- **시각화**: recharts, react-flow(Phase 3), @floating-ui/react

### 폴더 구조

```
src/
├── api/                    백엔드 통신 + MSW 모킹
│   ├── client.ts           ky 인스턴스
│   ├── *.ts                도메인별 API 클라이언트
│   └── mock/
│       ├── handlers/       라우트별 핸들러
│       └── fixtures/       더미 데이터 (이 폴더 통째로 삭제 = 실 백엔드 전환)
├── components/             UI 공통 컴포넌트
│   ├── ui/                 shadcn/ui (자동 생성)
│   ├── layout/             AppShell, Sidebar
│   ├── auth/               RequireAuth
│   ├── data-display/       ReviewCheckpoint, SourceCard, StatusDot, …
│   └── feedback/           EmptyState, LoadingSkeleton, ErrorBoundary
├── features/               도메인별 컴포넌트 (project-config, editor, progress, …)
├── pages/                  라우트별 페이지
├── hooks/                  useAuth, useWebSocket, useShortcut, useDebounce
├── lib/                    cn(), 유틸
└── styles/                 tokens.css, global.css

public/
├── mockServiceWorker.js    MSW 워커
└── samples/                HWPX·PDF·MD 더미 다운로드
```

### 디자인 시스템

`src/styles/tokens.css`의 CSS 변수가 단일 진실 원천.
`tailwind.config.ts`가 변수를 Tailwind 토큰으로 매핑.

- **금지**: `#hex` 하드코딩, 임의 인라인 색상, shadcn 의미 토큰 직접 사용 (`bg-accent`는 우리 브랜드 블루, shadcn의 muted hover 의미는 별도 매핑)
- **검수**: `pnpm preflight`가 `#hex` 색상 0건 자동 검출

---

## 백엔드 연결

### 개발 환경 (현재)

- MSW가 모든 `/api/*` 요청 가로채서 더미 응답
- 백엔드(`localhost:8000`) 안 띄워도 프론트만 띄울 수 있음
- WebSocket 시나리오도 MSW가 시뮬레이션

### 실 백엔드 연결 시점

도메인별로 점진 전환:
1. `src/api/mock/handlers/auth.ts` 핸들러 제거 → 실 `/auth/*` 사용
2. 동일 패턴으로 projects, sources, …
3. fixtures 폴더는 도메인별로 점진 삭제

운영 환경: 백엔드와 같은 도메인(ALB 통합) 전제. CORS 설정 불필요.
