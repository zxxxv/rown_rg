# Windows 개발 환경 셋업 가이드

본 가이드는 **Native Windows + Docker Desktop** 조합으로 프로젝트를 셋업·실행하는 절차를 정리합니다.
README의 명령어는 bash 기준이라 PowerShell에서 일부 다릅니다.

---

## 1. 사전 요구사항 (한 번만 설치)

| 도구 | 용도 | 설치 방법 |
|------|------|-----------|
| **Docker Desktop for Windows** | PostgreSQL + pgvector + pgroonga 컨테이너 실행 | <https://www.docker.com/products/docker-desktop> — 설치 시 "Use WSL 2 based engine" 옵션 그대로 두기 (기본값) |
| **Git for Windows** | 소스 코드 관리. Git Bash가 함께 설치되어 bash 호환 명령도 가능 | <https://git-scm.com/download/win> |
| **PowerShell 7+** | 기본 셸. Windows 11은 기본 포함, Windows 10은 별도 설치 권장 | Microsoft Store에서 "PowerShell" 검색 |
| **uv** | Python 패키지 매니저 | 아래 명령 1줄로 설치 |

### uv 설치

PowerShell에서:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

설치 후 새 PowerShell 창을 열어서 `uv --version` 동작 확인.

> Python 3.12는 따로 설치할 필요 없습니다. `uv sync`가 자동으로 가져옵니다.

---

## 2. 프로젝트 셋업 (PowerShell)

리포지토리를 받은 직후 1회만 실행합니다.

```powershell
# 1. 소스 받기
git clone <repo-url>
cd 02_rg_v2

# 2. Python 의존성 설치 (.venv 자동 생성)
uv sync

# 3. pre-commit hook 설치 (커밋 시 자동 검사)
uv run pre-commit install

# 4. 환경변수 파일 준비
Copy-Item .env.example .env
# 메모장 등으로 .env를 열고 ANTHROPIC_API_KEY, JWT_SECRET_KEY 채워 넣기

# 5. PostgreSQL 컨테이너 시작 (Docker Desktop이 켜져 있어야 함)
docker compose up -d

# 6. DB 마이그레이션
uv run alembic upgrade head

# 7. 백엔드 실행
uv run uvicorn src.main:app --reload
```

브라우저에서 <http://localhost:8000/health> 가 `{"status":"healthy","environment":"local"}` 반환하면 셋업 완료.

`Ctrl+C` 로 서버 종료.

---

## 3. bash ↔ PowerShell 명령어 대조표

README나 가이드 문서에서 bash 명령을 보면 아래 표를 참고해 PowerShell로 변환하세요.

| 동작 | bash | PowerShell |
|------|------|------------|
| 파일 복사 | `cp .env.example .env` | `Copy-Item .env.example .env` |
| 환경변수 inline 실행 | `ENVIRONMENT=production uv run uvicorn ...` | `$env:ENVIRONMENT="production"; uv run uvicorn ...` |
| 환경변수 해제 | `unset ENVIRONMENT` | `Remove-Item Env:ENVIRONMENT` |
| 환경변수 확인 | `echo $ENVIRONMENT` | `$env:ENVIRONMENT` |
| 프로세스 종료 | `pkill -f uvicorn` | `Stop-Process -Name python` (또는 터미널에서 `Ctrl+C`) |
| 파일 내용 보기 | `cat README.md` | `Get-Content README.md` (또는 `cat` alias 동작) |
| 디렉토리 목록 | `ls -la` | `Get-ChildItem -Force` (또는 `ls` alias) |
| 경로 구분자 | `/` | `\` 또는 `/` 모두 가능 (PowerShell이 자동 변환) |
| 출력 리다이렉트 | `cmd > file.txt` | `cmd > file.txt` (동일) |
| 백그라운드 실행 | `cmd &` | `Start-Process` 또는 별도 터미널 |

### curl 사용 시 주의

PowerShell의 `curl`은 실제로는 `Invoke-WebRequest`의 alias입니다. 옵션 문법이 bash의 curl과 다릅니다.

- **간단한 GET**: `curl http://localhost:8000/health` — 둘 다 동작
- **JSON POST**: 안전하게는 `Invoke-RestMethod` 사용
  ```powershell
  Invoke-RestMethod -Uri http://localhost:8000/api/v1/auth/login `
      -Method Post `
      -ContentType "application/json" `
      -Body '{"email":"me@test.com","password":"MyPass123!@"}'
  ```
- **bash 스타일 그대로 쓰고 싶다면**: Git Bash 터미널에서 실행 (Git for Windows에 포함됨)

---

## 4. 일상 작업 명령어

서버 실행 후 코딩 중 자주 쓰는 명령들 — Windows에서도 그대로 동작합니다.

```powershell
# 테스트 실행
uv run pytest tests/integration/test_week1_integration.py -v

# 커버리지
uv run pytest --cov=src --cov-report=html
# → htmlcov\index.html 을 브라우저로 열기

# 린트·포맷
uv run ruff check src/ tests/
uv run ruff format src/ tests/

# 마이그레이션 새로 생성
uv run alembic revision --autogenerate -m "메시지"
uv run alembic upgrade head

# DB 접속 (Docker 컨테이너 안의 psql)
docker exec -it 02_rg_v2-postgres-1 psql -U dev -d rown

# 초기 super_admin 생성
$env:INITIAL_ADMIN_EMAIL="me@test.com"
$env:INITIAL_ADMIN_PASSWORD="MyPass123!@"
uv run python scripts/create_initial_admin.py
```

---

## 5. 자주 만나는 문제와 해결

### "Cannot connect to Docker daemon"
- **원인**: Docker Desktop이 실행 중이 아님
- **해결**: 시스템 트레이에서 Docker Desktop 아이콘 확인 → 클릭 → "Engine running" 표시 대기 → 재시도

### `uv sync` 첫 실행이 수 분 걸림
- **원인**: 모든 wheel을 처음 다운로드·캐시 중 (정상)
- **참고**: 두 번째 실행부터는 1초 이내

### 포트 5432 충돌 ("port is already allocated")
- **원인**: 로컬에 PostgreSQL이 이미 설치·실행 중
- **해결**:
  ```powershell
  Get-Service -Name "postgresql*"
  Stop-Service -Name "postgresql-x64-16"   # 버전에 맞게
  ```
  또는 `services.msc` 에서 수동 중지

### 한글 파일명·문자열이 깨져 보임
- **원인**: PowerShell 5.1의 기본 인코딩이 CP949
- **해결**:
  - **PowerShell 7+ 사용** (기본 UTF-8) — 가장 권장
  - 또는 PowerShell 5.1에서 `chcp 65001` 실행 후 명령 진행

### `uv run pre-commit install` 실패
- **원인**: Git 인증 또는 권한 문제
- **해결**: Git for Windows에서 자격 증명 매니저 다시 설정, 또는 SSH 키 등록 후 재시도

### `docker compose up` 시 pgroonga 빌드가 느림
- **원인**: 첫 실행 시 `Dockerfile.postgres` 빌드 진행 중 (10-20분 가능)
- **해결**: 한 번 빌드되면 캐시되므로 다음부터는 즉시 시작. 진행 중인지 확인하려면 `docker compose logs -f postgres`

### `uv run pytest` 실행 시 `rown_test` DB 생성 실패
- **원인**: 테스트가 admin 권한으로 `CREATE DATABASE` 실행하는데 권한 부족
- **해결**: `.env`의 `DATABASE_URL`이 우리 docker postgres(`postgres://dev:dev@localhost:5432/rown`)를 가리키고 있는지 확인. dev 유저는 docker-compose에서 superuser로 설정됨.

---

## 6. 환경 검증 (Smoke Test)

셋업 후 한 번 돌려 모든 게 정상인지 확인:

```powershell
# 1. Docker 컨테이너 상태
docker compose ps
# postgres가 (healthy) 표시되어야 함

# 2. 백엔드 응답
curl http://localhost:8000/health
# {"status":"healthy","environment":"local"}

# 3. 통합 테스트 전체 통과 (약 35초)
uv run pytest tests/integration/test_week1_integration.py -v
# ============== 21 passed in 35.73s ==============
```

3가지 모두 통과하면 macOS/Linux 동료들과 정확히 같은 환경이 구성된 것입니다.

---

## 7. WSL2를 쓰고 싶을 때 (선택)

본 프로젝트는 Native Windows로 충분하지만, bash 명령을 그대로 쓰고 싶거나 Linux 환경이 익숙하다면 WSL2도 가능합니다.

- Windows에서 PowerShell로: `wsl --install Ubuntu-22.04`
- 이후 모든 명령을 WSL Ubuntu 내부에서 실행 (README의 bash 명령 그대로)
- Docker Desktop은 WSL과 자동 연동되므로 추가 설정 불필요

WSL2를 쓸지 안 쓸지는 **개인 선호**입니다. 팀 전체가 동일할 필요는 없습니다.
