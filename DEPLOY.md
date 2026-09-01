# 배포 가이드 — AWS Lightsail 단일 VM + `www.rowninsight.cloud`

한 대의 VM에 `docker compose`로 전체 스택(Postgres · 백엔드 · Caddy)을 올린다.
사용자는 `https://www.rowninsight.cloud` 하나로 접속하고, Caddy가 HTTPS·SPA 서빙·API 프록시를
모두 처리한다. 대상 규모: 사용자 ~10명.

```
[사용자] ── https ──▶ Caddy(80/443, 자동 HTTPS)
                       ├─ /            → web/dist (React SPA)
                       ├─ /api/* /ws/* /health → app:8000
                       └─ app ── Postgres
```

---

## 0. 전제 (한 번만)

- [ ] Lightsail 인스턴스 8GB/2vCPU/160GB (실행 중)
- [ ] **고정 IP(Static IP)** 인스턴스에 연결 — 재부팅해도 IP 유지 (안 하면 DNS가 끊김)
- [ ] Lightsail 방화벽: **80, 443 열기** / **22는 본인 IP만** / **8000·5432는 열지 말 것**
- [ ] 서버에 Docker + compose 플러그인 설치:
  ```bash
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker $USER   # 재로그인 후 sudo 없이 docker 사용
  ```

## 1. 코드 + 모델 전송

`models/`(int8 2개 ~1.1GB)·`web/.env.production`·`web/dist`는 `.gitignore` 대상이라
`git clone`엔 없다 → 별도로 챙긴다. `models/bge-m3-onnx-fp32`(2.2GB)는 운영에서 안 쓰므로
전송하지 않는다.

모델은 코드와 무관하니 **개발 중 미리** 스테이징에 올려두고, clone 후 제자리로 옮긴다:

```bash
# (a) 로컬 PC에서: int8 모델 2개를 스테이징으로 전송 (Windows PowerShell/Git Bash의 scp)
#     KEY = Lightsail .pem 경로 (예: Downloads/LightsailDefaultKey-ap-northeast-2.pem)
ssh -i <KEY.pem> ubuntu@<고정IP> "mkdir -p ~/models-staging"
scp -i <KEY.pem> -r \
  ./models/bge-m3-onnx-int8 ./models/bge-reranker-v2-m3-onnx-int8 \
  ubuntu@<고정IP>:~/models-staging/

# (b) 서버에서: 코드 clone (빈 디렉터리여야 함) 후 모델을 제자리로
git clone <레포 URL> ~/rown
mkdir -p ~/rown/models
mv ~/models-staging/* ~/rown/models/    # 같은 디스크라 즉시 이동(재복사 X)

# (c) web/.env.production 은 gitignore 라 clone 에 없다 → 서버에서 생성
cat > ~/rown/web/.env.production <<'EOF'
VITE_API_BASE_URL=/api/v1
VITE_WS_BASE_URL=/ws
EOF
```

> 프론트(`web/dist`)는 아래 3단계에서 서버에서 직접 빌드하므로 전송 불필요.

## 2. `.env` (프로덕션 값)

서버의 `~/rown/.env` 를 `.env.example` 기준으로 채운다. **반드시 바꿀 것:**

```dotenv
ENVIRONMENT=production
LOG_LEVEL=INFO

POSTGRES_DB=rown
POSTGRES_USER=rown
POSTGRES_PASSWORD=<강한 무작위 값>
# DATABASE_URL 은 compose 가 주입하므로 .env 값은 무시된다.

JWT_SECRET_KEY=<openssl rand -hex 32 결과, 32자 이상>   # 기본값/짧으면 부팅 차단됨
INTERNAL_API_KEY=<무작위 값>
SECRETS_ENCRYPTION_KEY=<Fernet 키>   # 시크릿 암호화 전용 — 비우면 JWT키에서 파생(로테이션 시 시크릿 깨짐)

REACT_FRONTEND_URL=https://www.rowninsight.cloud
SAML_BASE_URL=https://www.rowninsight.cloud

ANTHROPIC_API_KEY=<실제 키>          # 사용하는 provider 키만 채우면 됨
GEMINI_API_KEY=<실제 키>
OPENAI_API_KEY=<실제 키>
ORG_MONTHLY_COST_LIMIT_USD=<월 예산 상한>   # 10명이 보고서 돌리면 실제 과금 발생

# 네이버웍스(SSO/알림) — 안 쓰면 placeholder 라도 채워야 부팅됨
NW_CLIENT_ID=...
NW_CLIENT_SECRET=...
NW_SERVICE_ACCOUNT=...
NW_PRIVATE_KEY=...
NW_BOT_ID=...
```

시크릿 생성:
- `JWT_SECRET_KEY` / `INTERNAL_API_KEY`: `openssl rand -hex 32`
- `SECRETS_ENCRYPTION_KEY`: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`

## 3. 프론트엔드 빌드 (서버에서 컨테이너로)

호스트에 node 설치 없이 일회용 컨테이너로 빌드한다. `web/.env.production`(상대경로)이
Vite 빌드에 자동 반영된다 → `web/dist` 생성.

```bash
cd ~/rown
docker run --rm -e CI=true -v "$PWD/web":/w -w /w node:22-alpine \
  sh -c "corepack enable && pnpm install --frozen-lockfile && pnpm build"
```

> **이미지·플래그를 바꾸지 마라**(2026-08-25 배포에서 둘 다에 막혔다).
> - `node:22-alpine` — `web/package.json`의 `packageManager` 핀(pnpm 11)이 Node 22
>   이상을 요구한다(`node:sqlite`). `node:20-alpine`이면 pnpm이 기동조차 못 한다.
> - `-e CI=true` — pnpm이 다른 버전이 만든 `node_modules`를 지울지 물어보는데, 파이프
>   실행엔 TTY가 없어 `ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY`로 선다.
>
> pnpm 버전은 `packageManager`로 못 박혀 있다 — 핀이 없으면 corepack이 **매번 최신
> pnpm**을 끌어와, 어제 되던 빌드가 오늘 Node 요구사항이 올라갔다는 이유로 깨진다.
> 개발 PC와 서버가 같은 pnpm을 쓰게 하는 것이 핀의 목적이므로, 올릴 때는 둘을 같이 올린다.

> 대안: 개발 PC에서 `cd web && pnpm build` 후 `web/dist`를 rsync로 전송해도 된다.

## 4. DNS (인증서 발급 전에 반드시 전파 완료)

도메인 DNS 패널에서 A 레코드 2개를 **고정 IP**로:

| 이름 | 타입 | 값 |
|---|---|---|
| `rowninsight.cloud` | A | `<고정 IP>` |
| `www.rowninsight.cloud` | A | `<고정 IP>` |

전파 확인: `dig +short www.rowninsight.cloud` 가 고정 IP를 반환해야 한다.
**이게 끝나야** 다음 단계에서 Caddy가 Let's Encrypt 인증서를 발급받는다.

## 5. 기동

```bash
cd ~/rown
docker compose -f docker-compose.prod.yml up -d --build
```

순서는 compose가 보장한다: postgres(healthy) → migrate(alembic upgrade) → app → caddy.
Caddy 로그에서 인증서 발급 성공을 확인:

```bash
docker compose -f docker-compose.prod.yml logs -f caddy
```

## 6. 초기 데이터 (최초 1회)

```bash
cd ~/rown

# (1) super_admin 계정 생성
INITIAL_ADMIN_EMAIL=admin@rowninsight.cloud \
INITIAL_ADMIN_PASSWORD='<강한 비밀번호>' \
INITIAL_ADMIN_USERNAME=admin_rown \
  docker compose -f docker-compose.prod.yml run --rm app \
  uv run python scripts/create_initial_admin.py

# (2) IP 화이트리스트 시드 — 안 하면 /health 빼고 전원 403!
#     초기엔 전체 허용, 이후 admin UI에서 사무실 대역으로 좁힐 것.
#     IPv6 사용자까지 커버하려면 "0.0.0.0/0,::/0"
docker compose -f docker-compose.prod.yml run --rm \
  -e IP_WHITELIST_CIDR="0.0.0.0/0" app \
  uv run python scripts/seed_ip_whitelist.py
```

## 7. 스모크 테스트

```bash
curl -fsS https://www.rowninsight.cloud/health     # {"status":"healthy","environment":"production"}
```
브라우저에서 `https://www.rowninsight.cloud` → 로그인(admin_rown) → 프로젝트 1건 생성 →
보고서 풀사이클(HWPX까지) 1회 → admin 대시보드에 비용이 잡히는지 확인.

---

## 재배포 (코드 업데이트 시)

```bash
cd ~/rown
git pull
# 프론트 변경 시 3단계 재실행(pnpm build)
docker compose -f docker-compose.prod.yml up -d --build
# 새 마이그레이션이 있으면 migrate 서비스가 자동 실행됨
```

## 운영 메모

- **백업**: DB는 `./pgdata` 에 있다. 정기 백업 예시:
  ```bash
  docker compose -f docker-compose.prod.yml exec postgres \
    pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > backup_$(date +%F).sql.gz
  ```
- **IP 화이트리스트 좁히기**: 초기 `0.0.0.0/0` 시드 후, admin UI 또는 DB에서 사무실
  공인 IP 대역만 남기고 전체 허용 행을 비활성화한다. Caddy가 `X-Forwarded-For`를 실
  클라이언트로 덮어쓰므로 좁힌 규칙이 정확히 동작한다.
- **비용**: LLM 실호출은 실제 과금이다. `ORG_MONTHLY_COST_LIMIT_USD` 로 상한을 걸고,
  대시보드에서 당월 누적을 모니터링한다.
- **단일 워커**: uvicorn 단일 프로세스 전제(관리자 설정 인메모리 캐시 공유). 워커를
  늘리면 설정 반영 불일치가 생기므로, 스케일아웃 시 별도 설계가 필요하다.
