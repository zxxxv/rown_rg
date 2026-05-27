[![CI](https://github.com/zxxxv/rown_rg/actions/workflows/ci.yml/badge.svg)](https://github.com/zxxxv/rown_rg/actions/workflows/ci.yml)

# rown_rg

주식회사 로운인사이트 — AI 보고서 자동생성 시스템

## 기술 스택

- **Backend**: Python 3.12 · FastAPI · SQLAlchemy 2.0 (async) · Alembic
- **DB**: PostgreSQL 16 + pgvector + pgroonga (Docker)
- **LLM**: Anthropic Claude (Opus 4.7) · LangGraph (Phase 2 이후)
- **Auth**: JWT (python-jose) + bcrypt + 4-Role RBAC
- **Dev tools**: uv · ruff · pytest · pre-commit · structlog

## 개발 환경 셋업

처음 한 번만 실행!

```bash
# 1. 의존성 설치
uv sync

# 2. pre-commit hook 설치 (필수)
uv run pre-commit install

# 3. 환경변수 파일 준비
cp .env.example .env
# .env 안의 ANTHROPIC_API_KEY, JWT_SECRET_KEY 등을 채워 넣기

# 4. PostgreSQL 시작 (pgvector + pgroonga)
docker compose up -d

# 5. DB 마이그레이션
uv run alembic upgrade head

# 6. 백엔드 실행
uv run uvicorn src.main:app --reload
```

`http://localhost:8000/health` 가 200을 반환하면 정상

> **Windows 사용자**: 위 명령어는 bash 기준, PowerShell에서는 `cp` → `Copy-Item`,
> 환경변수 inline 실행 등 일부 명령이 달라서 자세한 절차와 명령어 대조표는
> [docs/Windows_셋업_가이드.md](docs/Windows_셋업_가이드.md) 참고

## 환경변수

`.env.example`을 `.env`로 복사해서 채워 넣기

| 변수 | 필수 | 기본값 | 설명 |
|------|:---:|--------|------|
| `DATABASE_URL` | ✓ | `postgresql+asyncpg://dev:dev@localhost:5432/rown` | DB 연결 문자열 |
| `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | ✓ | `rown` / `dev` / `dev` | docker-compose가 사용 |
| `JWT_SECRET_KEY` | ✓ | — | JWT 서명 키 (32자 이상 무작위 문자열) |
| `JWT_ALGORITHM` |  | `HS256` | JWT 알고리즘 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` |  | `60` | access token 만료 (분) |
| `REFRESH_TOKEN_EXPIRE_DAYS` |  | `7` | refresh token 만료 (일) |
| `ANTHROPIC_API_KEY` | LLM 호출 시 | — | Claude API 키 (`sk-ant-...`) |
| `LOG_LEVEL` |  | `DEBUG` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `ENVIRONMENT` | ✓ | `local` | `local` / `staging` / `production` |

## API 예시

```bash
# 1. 초기 super_admin 생성 (한 번만)
INITIAL_ADMIN_EMAIL=me@test.com INITIAL_ADMIN_PASSWORD='MyPass123!@' \
  uv run python scripts/create_initial_admin.py

# 2. 로그인 → access_token / refresh_token 받기
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "content-type: application/json" \
  -d '{"email":"me@test.com","password":"MyPass123!@"}'

# 3. access_token으로 내 정보 조회
curl -H "Authorization: Bearer <ACCESS_TOKEN>" \
  http://localhost:8000/api/v1/auth/me

# 4. 새 worker 등록 (admin 권한 필요)
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H "content-type: application/json" \
  -d '{"email":"w@test.com","password":"WorkerPass1!@","name":"W","role":"worker"}'
```

엔드포인트 전체 목록은 서버를 띄운 뒤 <http://localhost:8000/docs> (Swagger UI)에서 확인 가능

## pre-commit 안내

`.pre-commit-config.yaml`은 git에 올라가지만 hook 활성화는 각 개발자가 직접 하기
`uv run pre-commit install`을 안 돌리면 커밋 전 자동 검사(ruff, trailing-whitespace 등)가 작동 X

수동으로 전체 검사를 돌리려면:

```bash
uv run pre-commit run --all-files
```

## 테스트

PostgreSQL 컨테이너(`docker compose up -d`)가 떠 있어야 하고 테스트가 `rown_test` DB를 알아서 만들고 지움

```bash
# 전체
uv run pytest

# 통합 테스트만 (상세 출력)
uv run pytest tests/integration/ -v

# 커버리지 측정 (60% 이상 권장)
uv run pytest --cov=src --cov-report=html
# → htmlcov/index.html 을 브라우저로 열기
```

> 참고: pytest는 pre-commit hook에 넣지 않음. 커밋은 빠르게 가고, 전체 검증은 push 뒤 CI에서 자동으로 동작

## 프로젝트 구조

```
src/
├── api/                # FastAPI HTTP 계층
│   ├── dependencies/   # 라우터가 Depends로 받는 공통 의존성 (DB 세션, 인증, 권한)
│   ├── middleware/     # 로깅·IP 화이트리스트·에러 핸들러
│   ├── routers/        # 실제 엔드포인트 (auth, users — /api/v1 prefix)
│   └── schemas/        # Pydantic 입출력 DTO (도메인별 Base/Create/Update/Read)
├── clients/            # 외부 API 클라이언트 (Anthropic + cassette record/replay)
├── core/               # 앱 전역 설정·예외·로깅 셋업
├── db/                 # SQLAlchemy 비동기 엔진·세션
│   ├── models/         # ORM 모델 (DB 테이블 — 9종)
│   └── migrations/     # Alembic 마이그레이션
├── infrastructure/     # 외부 시스템 어댑터
│   └── auth/           # JWT · bcrypt · TOTP · lockout 핸들러
├── prompts/            # LLM 프롬프트
├── services/           # 도메인 서비스 — retrieval / generation / qa / indexing
├── workflows/          # LangGraph 그래프
└── main.py             # FastAPI 진입점 (lifespan · 미들웨어 등록 · /health)

tests/
├── conftest.py         # 공통 fixture (테스트 DB, AsyncClient, 4-Role user/token)
├── integration/        # 통합 테스트 (API + DB end-to-end)
├── unit/               # 단위 테스트
└── golden_dataset/     # LLM 출력 검증용 데이터셋

scripts/                # 운영·관리 스크립트 (초기 super_admin 생성 등)
docs/                   # 설계서·회의록·가이드 (대부분 .gitignore — Windows 가이드만 추적)
init-scripts/           # PostgreSQL 컨테이너 초기화 SQL (확장 활성화)
```

### 핵심 디자인 원칙

- **API 표면(`src/api/schemas`) ≠ DB 모델(`src/db/models`)** — Pydantic DTO와 SQLAlchemy ORM을 따로, 같은 도메인이어도 입력·출력·저장 형태가 다 다르기 때문
- **인프라(`src/infrastructure/`)와 도메인(`src/services/`) 분리** — JWT·bcrypt·LLM API 같은 외부 의존이 바뀌어도 도메인 로직이 영향받지 않게
- **`src/clients/` 의 record/replay 모드** — 테스트랑 로컬에선 Anthropic API를 부르지 않음
- **미들웨어 ↔ Depends 분리** — 모든 요청에 공통으로 거는 건(로깅, IP 검사) 미들웨어로, 라우터별 정책(인증, 권한)은 `Depends`로 처리

### 4-Role 권한 매트릭스

|  | super_admin | admin | worker | viewer |
|---|---|---|---|---|
| 사용자 관리 | ✓ | ✓ | | |
| 사용자 삭제 | ✓ | | | |
| 자신의 프로젝트 CRUD | ✓ | ✓ | ✓ | |
| 프로젝트 읽기·보고서 다운로드 | ✓ | ✓ | ✓ | ✓ |

> 자료 라이브러리·프로젝트 자료 도메인 모델은 프론트 mock에서 먼저 잡혀 있음 —
> 백엔드 도입 시 [`web/src/api/types.ts`](web/src/api/types.ts) (`LibraryFileMeta`, `Source`) 참고.

## 기여 가이드

### 브랜치 전략

- `main` — 항상 배포 가능한 상태 (직접 push 금지)
- `feature/<task-name>` — 새 기능·작업
- `fix/<issue>` — 버그 수정
- `chore/<task>` — 의존성 업데이트·설정 변경 등

### PR 체크리스트

1. 로컬에서 `uv run ruff check src/ tests/` 통과
2. 로컬에서 `uv run pytest` 통과 (또는 push 후 CI 확인)
3. PR 제목과 본문에 변경 사유·테스트 방법 명시
4. CI 그린 확인 후 머지 (직접 main push 금지)

### 일반 원칙

- PR 하나에는 한 가지 변경만. 무관한 수정은 섞지 않기
- pre-commit hook은 처음에 `uv run pre-commit install` 한 번 돌리면 그 뒤로 자동
- 시크릿(`.env`, API 키)은 절대 커밋 금지

## CI (GitHub Actions)

push와 PR마다 자동으로 아래 검사가 동작 (`.github/workflows/ci.yml`):

- `uv sync --frozen` — `uv.lock`이 최신인지
- `ruff check` / `ruff format --check` — 린트 + 포맷
- `pytest --cov=src --cov-fail-under=60` — 통합 테스트 + 커버리지 60% 게이트

PostgreSQL은 `Dockerfile.postgres`로 만든 이미지(pgvector + pgroonga 포함)를 GHCR에서 받아 사용

### CI가 빨간색일 때

PR 페이지의 ✗를 클릭하면 실패 로그가 보입니다. 자주 만나는 경우:

- **`uv.lock` 어긋남** — 로컬에서 `uv add/remove` 한 뒤 `uv.lock` 커밋 깜빡한 경우 → `git add uv.lock && git commit --amend`
- **ruff 위반** — `uv run ruff check --fix src/ tests/ scripts/`로 자동 수정 후 재커밋
- **테스트 실패** — 로컬에서 `uv run pytest`로 재현해서 수정
- **커버리지 60% 미달** — 새 코드에 테스트 추가. 정 안 되면 게이트를 잠시 낮추는 PR을 따로 올리기
