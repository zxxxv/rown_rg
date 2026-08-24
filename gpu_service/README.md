# GPU 리랭킹 서비스

로컬 RTX 3060 Ti를 도커로 띄우고, AWS의 보고서 앱이 재채점만 이리로 보낸다.

## 처음부터 끝까지 (GPU 박스에서)

도커만 있으면 된다. 파이썬·CUDA 툴킷을 따로 깔지 않는다.

```
git clone <저장소> && cd rown_rg && git checkout feat/gpu-inference
cp gpu_service/.env.example gpu_service/.env      # GPU_TOKEN 채우기
docker compose -f gpu_service/docker-compose.yml --profile setup run --rm exporter
docker compose -f gpu_service/docker-compose.yml up -d --build
curl -s localhost:8009/health
```

내려받는 양이 커서(내보내기 이미지·CUDA 런타임 이미지·모델 합쳐 약 8GB) 회선에
따라 30분~1시간을 잡는다. 그 뒤로는 재시작 몇 초다.


## 왜

2026-08-20 탄소규제 3차 런 실측(절 20 · 청크 1,498):

| 구간 | 실측 | 병목 |
|---|---|---|
| 리허설 | 절당 140초 × 20절 ≈ **47분** | 리랭킹이 지배 |
| 업로드 색인(13 PDF) | 24분 44초 | 대부분 임베딩 |

절당 140초의 정체는 `fetch_k = max(30, top_k × 에이전트수 × 2)` = **후보 96개**를
cross-encoder가 전부 재채점하는데 최종 채택은 24개라는 것이다. 3060 Ti면 47분이
2~4분으로 내려간다.

**리랭커부터 옮기는 이유**: 리랭커 점수는 순위 결정에만 쓰이고 **저장되지 않는다**.
임베딩은 다르다 - AWS의 기존 색인은 CPU int8 벡터라 GPU fp16 질의 벡터를 그 공간에
던지면 조용히 검색이 나빠진다. 임베딩 원격화는 dtype 오차를 벤치로 재고
(`scripts/benchmark_dtype_device.py`), 넘으면 전량 재색인해야 한다.

## 1. GPU 박스 준비

- NVIDIA 드라이버 525 이상 (CUDA 12는 마이너 버전 호환이라 12.x 런타임이 모두 돈다)
- Docker Desktop + WSL2 백엔드
- GPU가 컨테이너에서 보이는지 먼저 확인:

```
docker run --rm --gpus all nvidia/cuda:12.6.3-base-ubuntu24.04 nvidia-smi
```

여기서 안 보이면 서비스는 뜨긴 뜨지만 **CPU로 조용히 떨어진다**. 그 상태는
`/health`의 `on_gpu: false`로 드러난다.

## 2. 모델 준비 (도커만으로)

저장소의 `models/bge-reranker-v2-m3-onnx-int8`은 **CPU 전용**이다(INT8 양자화 연산자).
GPU용 내보내기를 따로 만들어야 하고, `models/`는 git에 없으므로 **클론만으로는 안 생긴다**.

파이썬을 안 깔고 도커로 만든다(일회성 컨테이너, 끝나면 사라진다):

```
docker compose -f gpu_service/docker-compose.yml --profile setup run --rm exporter
```

허깅페이스에서 받아 `models/bge-reranker-v2-m3-onnx`(약 2.3GB, fp32)로 내보낸다.
fp32는 수치가 안 바뀌어 안전하다 - 여기서 시작한다.

fp16(약 1.1GB, 텐서코어로 더 빠름)을 쓰려면 인자를 덧붙인다:

```
docker compose -f gpu_service/docker-compose.yml --profile setup run --rm exporter \
    --out /models/bge-reranker-v2-m3-onnx-fp16 --fp16 --verify
```

`--verify`는 기존 INT8 모델과 같은 문장쌍을 채점해 순위가 뒤집히는지 본다.
`★ 뒤집힘 ★`이 뜨면 그 dtype은 쓰지 않는다. fp16을 쓰기로 했으면
`docker-compose.yml`의 `GPU_MODEL_DIR`을 그 폴더로 바꾼다.

저장소 venv가 있는 곳(예: 개발 PC)에서 만들어 폴더째 복사해도 된다:

```
uv run python gpu_service/scripts/export_reranker_onnx.py \
    --out models/bge-reranker-v2-m3-onnx --verify
```

## 3. 실행

```
cp gpu_service/.env.example gpu_service/.env
# GPU_TOKEN 을 채운다:  openssl rand -hex 32
docker compose -f gpu_service/docker-compose.yml up -d --build
```

토큰이 비어 있으면 **뜨지 않는다**(`GPU_ALLOW_ANON=1`을 명시하지 않는 한).
이 엔드포인트로는 자료 본문이 오간다.

## 4. 검증

```
curl -s localhost:8009/health | python -m json.tool
```

`ready: true`, `on_gpu: true`, `providers`에 `CUDAExecutionProvider`가 있어야 한다.
`on_gpu: false`면 CPU로 떨어진 것이고 속도 이득이 없다.

실제 채점까지 확인:

```
curl -s -X POST localhost:8009/v1/rerank \
  -H "Authorization: Bearer $GPU_TOKEN" -H 'Content-Type: application/json' \
  -d '{"query":"탄소국경조정제도 적용 품목","passages":["CBAM은 시멘트·철강·알루미늄·비료·전력·수소를 대상으로 한다.","이 보고서의 표지는 국문과 영문을 병기한다."]}'
```

첫 문단 점수가 둘째보다 **뚜렷이 높아야** 한다. 비슷하면 모델 폴더가 잘못됐다.

## 5. AWS 앱 연결

앱은 `settings.reranker_remote_url`이 비어 있으면 지금처럼 로컬 CPU 모델을 쓴다.
채우면 팩토리가 원격으로 분기한다(`src/clients/reranker_factory.py`).

```
RERANKER_REMOTE_URL=http://<터널주소>:8009
RERANKER_REMOTE_TOKEN=<GPU_TOKEN 과 같은 값>
RERANKER_REMOTE_TIMEOUT_S=60
RERANKER_REMOTE_FALLBACK=local
```

### 네트워크 - 포트포워딩 금지

로컬 PC는 대개 NAT 뒤라 AWS가 먼저 들어올 수 없다. **공유기 포트포워딩으로 이
엔드포인트를 인터넷에 여는 것은 하지 말 것** - 자료 본문이 오가는 경로다.
**Tailscale** 또는 **Cloudflare Tunnel**로 사설망을 잇는다. 데이터량은 문제가
아니다(리랭킹 절당 약 50KB).

컨테이너 포트는 `127.0.0.1:8009`에만 묶여 있다. 두 터널 모두 **호스트에서 루프백으로**
붙기 때문에 이대로 동작한다 - `0.0.0.0`으로 바꾸지 말 것(사내망 전체에 열린다).

```
tailscale serve --bg 8009
# 또는
cloudflared tunnel --url http://127.0.0.1:8009
```

### 역터널 상시화 (2026-08-25)

터널이 조용히 죽으면 앱은 계속 폴백하는데 이쪽은 살아 있는 걸로 보인다. 다음
세 겹으로 막는다. **keepalive는 양쪽이 짝**이어야 한다 — 한쪽만 켜면 반쪽이다.

| 층 | 설정 | 복구 시간 |
|---|---|---|
| 죽은 연결 감지 (박스) | `ServerAliveInterval=15` `ServerAliveCountMax=3` | 45초 |
| 죽은 세션 회수 (서버) | `ClientAliveInterval 15` `ClientAliveCountMax 3` | 45초 |
| ssh 프로세스 사망 | `rown-tunnel.ps1`의 재접속 루프(5초 대기) | **실측 5.7초** |
| 감시 스크립트 사망 | 작업 스케줄러 재시작(999회 × 1분) | 1분 |
| 태스크 자체 정지 | 15분 간격 반복 트리거 + `IgnoreNew` | 15분 |

**서버 쪽 `ClientAliveInterval`이 0이면 안 된다.** 박스가 갑자기 사라지면
(정전·NAT 끊김) sshd가 죽은 세션을 붙들어 8009 포트가 계속 묶이고, 박스가
재접속해도 `ExitOnForwardFailure`로 튕겨 나가 복구가 TCP 타임아웃(최대 ~15분)
까지 늦어진다. 2026-08-25까지 실제로 0이었다. 설정은
`/etc/ssh/sshd_config.d/60-gpu-tunnel.conf`, 적용은 `sshd -t`로 검증한 뒤
`systemctl reload ssh`(reload는 기존 세션을 끊지 않는다).

`ServerAliveInterval`이 15초인 또 다른 이유: 공유기 NAT의 유휴 세션 타임아웃
(보통 30초~5분)보다 짧아야 조용한 시간대에 매핑이 지워지지 않는다.

**스크립트를 고쳤으면 태스크를 재시작해야 한다.** 실행 중인 PowerShell은 시작
시점의 스크립트를 메모리에 들고 있어, 파일만 고치고 ssh를 죽이면 **옛 인자로**
재접속한다(2026-08-25에 밟았다). `Stop-ScheduledTask` → `Start-ScheduledTask`.

### 부팅 후 자동 복구 — Windows의 한계

박스가 Windows라 systemd 유닛이 없다. 대응물은 이렇다:

| systemd | 여기서는 |
|---|---|
| `Restart=always` / `RestartSec=5` | `rown-tunnel.ps1`의 `while($true)` + 5초 대기 |
| 부팅 시 유닛 기동 | Docker Desktop 자동 시작(HKCU Run) + 컨테이너 `restart: unless-stopped` |
| 터널 유닛 | 작업 스케줄러 `rown-gpu-tunnel`(로그온 트리거 + 15분 감시) |

**한계: Docker Desktop은 사용자 세션 앱이라 로그인 전에는 뜨지 않는다.** 그래서
"부팅 상시화"의 실제 의미는 *로그인 후 무개입 복구*다. 로그인 없는 진짜 상시화가
필요하면 자동 로그인을 켜거나(보안 대가), Docker Desktop을 걷어내고 WSL2 +
Docker CE + systemd로 옮겨야 한다(별도 작업).

**기동 순서(VRAM 8GB 공유):** gpu_service가 먼저다 — 리랭커·임베딩·파서로
약 5.2GB를 쓴다. 로컬 판정 LLM(2~4B) 실험은 **뒤에 수동으로** 띄우고
`restart:` 정책을 주지 않는다. 정책을 주면 부팅 때 둘이 경쟁해 gpu_service가
VRAM 부족으로 죽어도 조용히 CPU로 떨어진다. 실험 전 `/health`의
`gpu.memory_used_mib`로 여유를 먼저 확인할 것.

### 폴백 두 가지

| 값 | 동작 | 대가 |
|---|---|---|
| `local` (기본) | 서버 CPU 모델로 다시 채점 | 품질 동일. **서버가 모델 메모리 피크를 감당할 크기를 유지해야 한다** |
| `passthrough` | 재채점 생략, 검색 순위 통과 | 품질 소폭 하락. 서버에서 모델을 완전히 들어낼 수 있다 |

Lightsail을 8GB($44)까지 내리려면 `passthrough`가 필요하다. 16GB($84)까지는
`local`을 유지해도 된다. 원격이 안정된 뒤에 바꾸는 순서를 권한다.

원격이 실패하면 `reranker_remote_cooldown_s`(기본 60초) 동안 원격을 건너뛴다.
이게 없으면 죽은 서비스를 20절 × 60초 = 20분 동안 기다린다.

## 문제 해결

**`on_gpu: false`** - `--gpus all` 확인, nvidia 런타임 확인, 그리고 이미지의 CUDA
태그가 드라이버보다 높지 않은지. 로그에 `CUDA를 요청했으나 CPUExecutionProvider로
떨어졌습니다`가 남는다.

**VRAM OOM** - `GPU_BATCH_SIZE`를 32 → 16 → 8로 낮춘다. 8GB에 512토큰 × 32가 안전선이다.

**토크나이저가 네트워크로 나가려 함** - 이미지는 `HF_HUB_OFFLINE=1`이라 즉시 실패한다.
내보낸 폴더에 `tokenizer.json`이 없다는 뜻이니 2단계를 다시 한다.

**"Already borrowed" 크래시** - HF fast tokenizer는 Rust 백엔드라 동시 호출에 죽는다
(2026-08-12 실사고: 자료 41개 중 4개만 색인되고 3시간 정지). `OnnxCrossEncoder`가
토큰화에 락을 걸고 있고, `GPU_MAX_CONCURRENCY=1`이 한 겹 더 막는다. 이 둘을 풀지 말 것.

## 구조

```
gpu_service/app/main.py       FastAPI - /health, /v1/rerank
gpu_service/app/service.py    모델 보유 + 세마포어
gpu_service/app/config.py     환경변수만 (앱 Settings를 쓰지 않는다)
src/clients/onnx_cross_encoder.py   ← 채점 알맹이. 앱과 이 서비스가 같이 쓴다
```

알맹이를 공유하는 이유: 토크나이즈·배치·시그모이드가 두 벌로 갈라지면
`max_length` 한 자리 차이로 **에러 없이 순위만 달라진다**. 그 파일은 `src.core.config`를
import하지 않는 것이 계약이고, 그래서 이 컨테이너에 그 파일만 복사할 수 있다.

## 모니터링 (2026-08-20)

세 겹으로 본다. 겹마다 답하는 질문이 다르다.

| 어디서 | 무엇을 | 왜 따로인가 |
|---|---|---|
| 앱 `/admin/gpu` (관리자 화면) | 큐·하드웨어 최근 1시간 + **앱 폴백 누적** | 폰에서도 밖에서도 보인다. 폴백은 앱만 안다 |
| netdata (GPU 박스 `127.0.0.1:19999`) | 하드웨어(nvidia_smi) + 서비스 지표 장기 보관·알림 | 서비스가 죽어도 하드웨어는 계속 보인다 |
| `/health` (터널 경유) | 즉석 스냅샷 | 스크립트·헬스체크용 |

서비스가 내는 엔드포인트:

- `GET /stats/history` — 5초 간격 1시간 링버퍼, 필드별 병렬 배열. 관리자 화면의 그래프 원천.
- `GET /metrics` — 프로메테우스 텍스트. **하드웨어 수치는 없다**(netdata nvidia_smi가 이미 뜬다).
  서비스만 아는 것을 낸다: 큐 상태와 `on_gpu` 플래그. onnxruntime이 어긋나 CPU로 떨어지면
  GPU 지표는 그저 한가해 보인다 — 이 플래그가 "못 쓰는 중"과 "안 쓰는 중"을 가른다.

netdata 쪽 설정은 **컨테이너 볼륨(`netdataconfig`)에 있고 이 레포에는 없다.** 재구축 시 복원:

```yaml
# /etc/netdata/go.d/prometheus.conf
jobs:
  - name: rown_gpu
    url: http://host.docker.internal:8009/metrics
```

```
# /etc/netdata/health.d/rown_gpu.conf — 알림 두 개
#  rown_gpu_on_cpu      (crit)  on_gpu < 1 이 1분 지속 - 조용한 CPU 폴백
#  rown_gpu_rejections  (warn)  최근 10분 429 거절 > 0 - 용량 재검토 신호
# 템플릿은 차트 id가 아니라 context(prometheus.rown_gpu.*)에 붙는다.
```

적용은 `netdatacli reload-health`(알림) / `docker restart netdata`(수집 작업).
