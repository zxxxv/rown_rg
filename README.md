# rown_rg

주식회사 로운인사이트 — AI 보고서 자동생성 시스템

## 개발 환경 셋업

최초 클론 후 1회 실행:

```bash
# 1. 의존성 설치
uv sync

# 2. pre-commit hook 설치 (필수)
uv run pre-commit install

# 3. 환경변수 파일 준비
cp .env.example .env
# .env의 ANTHROPIC_API_KEY, JWT_SECRET_KEY 등을 채워 넣기

# 4. PostgreSQL 시작 (pgvector + pgroonga)
docker compose up -d

# 5. DB 마이그레이션
uv run alembic upgrade head

# 6. 백엔드 실행
uv run uvicorn src.main:app --reload
```

`http://localhost:8000/health` 가 200을 반환하면 정상입니다.

## pre-commit 안내

`.pre-commit-config.yaml`은 git에 올라가지만 **hook 활성화는 각 개발자가 직접 해야** 합니다.
`uv run pre-commit install`을 안 돌리면 커밋 전 자동 검사(ruff, trailing-whitespace 등)가 작동하지 않습니다.

수동으로 전체 검사를 돌리려면:

```bash
uv run pre-commit run --all-files
```
