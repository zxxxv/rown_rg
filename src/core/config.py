from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


# config.py 위치 기준으로 프로젝트 루트(.env 위치)를 고정 — 실행 CWD와 무관하게 .env를 찾는다.
# config.py = <root>/src/core/config.py 이므로 parents[2] = <root>.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_JWT_SECRET = "change-me-32-chars-or-more"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 환경
    environment: Environment = Environment.LOCAL
    log_level: str = "DEBUG"

    # DB
    database_url: str = "postgresql+asyncpg://dev:dev@localhost:5432/rown"
    postgres_db: str = "rown"
    postgres_user: str = "dev"
    postgres_password: str = "dev"

    # LLM
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    openai_api_key: str = ""

    # LLM 역할별 기본 모델 — 테스트·비용 제어 시 .env로 교체 (예: claude-haiku-4-5)
    planner_model: str = "claude-sonnet-4-6"
    # 수집만 Haiku(2026-08-08 결정): 수집은 질의 생성·도구 오케스트레이션·매니페스트
    # JSON이라 상위 모델 이점이 작고, 부실해도 **작성 앞의 자료 검토 게이트**가 막아
    # '추가 조사'로 복구된다(하방 차단). 런당 ~$3.6 절감. 판정 지표는 회수 건수가 아니라
    # 미커버 절 수 — 게이트에서 실전 확인하고, 뚜렷이 나쁘면 sonnet으로 되돌린다.
    research_model: str = "claude-haiku-4-5"
    write_model: str = "claude-sonnet-4-6"
    # 작성 콜 추론(thinking) 깊이 — thinking 기본-on 모델(Opus 5)에만 적용된다
    # (services/generation/effort.py). 근거가 통째로 프롬프트에 들어가는 절 작성은
    # 추론 집약형이 아닌데 기본 effort(high)로 돌면 출력 과금의 절반 이상이 추론
    # 토큰이었다($33.84 런 분해, 2026-08-14). low로 낮추면 작성 -$8~10/런 추정.
    # A/B: 서버에서 절 1~2개 재작성 후 육안 비교 — 품질이 떨어지면 medium으로 올린다.
    write_effort: str = "low"
    # 종합 절(builds_on 보유 = 앞 절들을 받아 결론을 내는 임무) 전용 상향(2026-08-20
    # 사용자 가설: low가 시사점류의 통찰 깊이를 깎았을 수 있다 - 8/14 A/B는 일반 절
    # 기준이라 종합 절은 미검증). 20절 중 4~6절만 해당해 비용 +$2~3/런. 6차 검증 축.
    write_effort_synthesis: str = "medium"
    # Fable 5 전용 추론 깊이 — Opus와 독립 튜닝한다(2026-08-20 결정). Fable은 추론을
    # 끌 수 없고(설정 시 400) 같은 effort에서도 Opus보다 토큰이 더 나올 수 있어,
    # 한 키를 공유하면 한쪽을 조일 때 다른 쪽이 딸려 온다. 기본값은 Opus와 같은
    # 자리에서 출발하되 실전 비용을 보고 이 두 줄만 조정한다.
    fable_write_effort: str = "low"
    fable_write_effort_synthesis: str = "medium"
    # 웹 리서치 비용 노브 — 검색/회수 횟수와 응답 상한 (챕터당 1콜 분할 수집이라
    # 총 수집 폭 = 챕터 수 × max_uses)
    # 절당 후보 수. 1 = 기본(2026-08-07 확정). 후보 2개는 비용을 두 배로 쓰면서
    # 값어치가 없었다 — 사람이 게이트에서 정독하지 못하고(원격 14절 11만 자를 29분)
    # 길이로 골랐고, 그렇게 고른 후보가 오히려 고유출처 밀도가 낮았다(1.10 vs 1.52,
    # 21/28절). n=1은 HARD 실패 시 대체 후보가 없으므로 write_retry_on_empty와 짝이다.
    write_candidates_n: int = 1
    # n=1에서 정적 게이트 HARD 전멸 시 절 단위 1회 재생성. 없으면 그 절이 비고
    # 조립의 structure_complete가 렌더를 통째로 스킵한다.
    write_retry_on_empty: bool = True
    # 절 단위 병렬 작성 동시성 상한(세마포어). 절끼리는 의존성이 없어 병렬이 안전하고,
    # 상한은 LLM 레이트리밋 보호다 — 429가 잦으면 낮춘다(.env WRITE_SECTION_CONCURRENCY).
    # 절 내부의 분할 파트는 문맥 연결 때문에 여전히 직렬이다.
    write_section_concurrency: int = 4
    # 서사 사슬(실험 C, 2026-08-20 설계): 앞 절의 수치 금지 요약을 다음 절에 넘긴다.
    # - "off": 현행 평면/배치 병렬(기본)
    # - "chapter": 장 안만 순차, 장 간 병렬 — 세마포어 4·장 4개면 벽시계가 평면
    #   병렬과 같다(20절/4=5라운드). 채택 후보 형태.
    # - "full": 전면 순차(누적 전달) — 장 간 서사까지 잇는 대신 벽시계 4배. chapter가
    #   장 안을 잡고도 장 간 잔여가 클 때 꺼내는 검증 카드.
    write_narrative_chain: str = "off"
    # 3층 국소 재작성(조립 시점) - 절 간 중복 문단을 "(X.Y절 참조)" 압축으로 소거.
    # 골든 셋 통과(2026-08-20, v5c-2 실물 5/5 적용·격식 유지·고유 몫 보존·복제
    # 349->320)로 기본 on. 결정적 3중 검증+캡이 안전망, 실패는 전부 원문 유지.
    write_dedup_rewrite: bool = True
    # 분할 생성 — 단일 LLM 호출은 재료·캡과 무관하게 4~8천자에서 멈춘다(2026-08-07
    # top_k 곡선 실측). volume_target min이 그 한계를 넘는 절은 소주제 파트로 나눠
    # 순차 생성 후 결합한다(프로토타입: 분량 7배·무근거 밀도 flat 이하, exp_split2).
    # 파트 수 = clamp(ceil(min_chars / chars_per_part), 1, max_parts).
    # 2250·10 = 파트 배수 실험(2026-08-08) 반영: 파트를 2배로 잘게 쪼개면 분량이
    # 52% 늘고(7,602→11,564자) 인용 분산·무근거도 개선된다. 분석 틀(STEEP·SWOT)이
    # 있는 절은 파트 수가 축 수보다 적으면 축이 잘리므로 여유가 필요하다(실측: 3파트에
    # STEEP 5축 불가 → 6파트에서 전축 커버). 상한 10 = min 20,000자 절의 9파트 수용.
    write_split_enabled: bool = True
    write_split_chars_per_part: int = 2250
    write_split_max_parts: int = 10
    research_max_uses: int = 5
    # 회수(web_fetch) 전용 횟수 — 검색과 분리한다.
    # 5 = 검색과 동수(2026-08-07 복원). PDF는 이제 web_fetch를 타지 않고 우리가 직접
    # 내려받으므로(services/research/pdf_fetch) 요청당 100페이지 상한 위험이 사라졌다.
    # 임시로 2까지 조였더니 PDF 문제는 사라졌지만 HTML 회수가 목 졸려 자료가 9건에
    # 그쳤다(기준선 18건) — 병목이 방어막 자체였다. 모델이 지시를 어기고 PDF를 회수해도
    # stages._collect_chapter의 재시도(fetch 1회)가 받아낸다.
    research_max_fetch_uses: int = 5
    research_max_tokens: int = 8000
    # 챕터 1콜의 벽시계 상한(초). 수집은 서버측 web_search·web_fetch를 여러 번 도는
    # 에이전틱 루프라 응답이 영원히 안 오는 상태가 실제로 나온다(2026-08-10 실측:
    # 17건 수집 후 28분 무활동, 화면은 '수집 중' 그대로). 넘기면 그 챕터만 버리고
    # 다음 챕터로 간다 — 챕터 실패는 이미 격리돼 있다.
    research_chapter_timeout_seconds: int = 480
    # 자료 풀 권장 하한 = 초기 수집 목표 — 미달이어도 차단하지 않고 게이트에 경고 표시.
    # 40 = 분량 레버(2026-08-06): 절당 근거 공급이 분량의 1차 병목이라 자료 풀을 2배로.
    # 웹 검색 지역화 힌트(ISO alpha-2). 비우면 지역 편향 없이 전 세계에서 찾는다.
    # "KR"로 고정돼 있어 국내 자료로 기울었다 — 해외 기관·학술 자료도 같은 무게로
    # 봐야 하는 보고서(기술 동향·주요국 정책 비교)가 손해였다(2026-08-11).
    research_user_country: str = ""
    research_min_sources: int = 40
    # '추가 조사' 한 라운드의 신규 출처 목표 — 사람이 누를 때마다 이만큼씩 보충
    research_more_batch: int = 10
    # 절당 검색 근거 청크 수(리랭크 후 top-k).
    # 16→32(2026-08-08): 단일 호출 시절엔 재료 증량이 역효과였지만(top_k 곡선: 2.2배→
    # 분량 -17%), 파트마다 근거를 배타 배정하는 분할 생성에서는 비로소 소화된다 —
    # 실측 분량 +10%, 고유 출처 5→8, 무근거 0.17→0.08. 리랭커 시간이 절당 ~50초 든다.
    # 이제 '상한 캡'이다 - 실제 개수는 리랭커 점수 분포가 정한다(retrieval_score_ratio).
    # 32→24: 고정 32는 확신 구간을 넘어 아래쪽을 노이즈로 채웠다(2026-08-12 실측:
    # 평균 점수 0.469, 하위 10% 0.011). 캡 24 + 비율 0.10이면 절당 중앙 22.5개가
    # 남고 평균 0.746·하위 10% 0.301이 된다.
    # 절 질의 분화 — 절 제목 하나로만 찾던 것을 핵심 포인트·에이전트 관점 질의로 넓힌다.
    # off로 되돌리면 옛 동작(질의 1개) 그대로라 A/B·통제 실험이 이 한 줄이다.
    retrieval_multi_query_enabled: bool = True
    retrieval_top_k: int = 24
    # 상위 1위 점수 대비 하한 비율. 절대값은 질의마다 점수 스케일이 흔들려 못 쓴다 -
    # 어떤 절은 전부 통과하고 어떤 절은 전부 잘린다. 재현성을 위해 방식은 상수로 둔다.
    retrieval_score_ratio: float = 0.10
    # 한 출처가 절당 근거 슬롯에서 가져갈 수 있는 최대 비율. 리랭커는 자료 단위를
    # 모르므로 청크가 많은 자료 하나가 절마다 상위를 도배한다 - 탄소규제 런(2026-08-14)
    # 실측에서 자료 1건이 전체 인용의 28%, 상위 3건이 52%였고 4청크짜리 핵심 자료
    # (K-RE100 이행수단)는 단 한 번도 안 뽑혔다. 슬롯을 나눠 작은 자료에 자리를 남긴다.
    # 자료가 적은 프로젝트에서 상한이 근거를 굶기지 않도록 남는 슬롯은 순위대로 되채운다.
    # 0.25 → 0.20 (2026-08-20): 절당 상한으로는 문서 수준 누적을 못 막는다 - 3차 런
    # 실측에서 같은 자료가 20개 절마다 상한(6/24)을 채워 전체 인용의 19%(x518)를
    # 먹었다(v2 13%에서 악화 - 질의 분화가 넓은 자료를 더 여러 각도로 걸어 올림).
    # 0.20이면 절당 4슬롯(하한 3 유지). 근거 굶김 위험은 낮다: v3 절당 고유자료 8.75.
    retrieval_source_cap_ratio: float = 0.20
    # 전역 동시 파이프라인 실행 상한 — 초과분은 FIFO 대기열(runner._run_slots).
    # 색인(임베딩 ONNX)이 CPU·메모리를 지배해 운영 스펙(2 vCPU/8GB) 기준 1이 안전선.
    max_concurrent_runs: int = 1
    # 섹션 작성 출력 상한(토큰) — 에이전트 volume_target이 있어도 이 값으로 캡.
    # 24000 ≈ 한글 1.5~2만자 헤드룸(분량 레버와 정합 — 근거를 늘려도 캡이 막지 않게).
    # 저비용 테스트 시 .env로 하향(예: 4096)
    write_max_tokens: int = 24000
    # Anthropic SDK 요청 타임아웃(초). SDK 내부 재시도는 0으로 고정한다 —
    # 재시도는 어댑터가 소유하며, SDK가 타임아웃된 고비용 서버도구 턴을 조용히
    # 재실행하면 과금만 배가된다(2026-07-21 스모크에서 실측).
    llm_client_timeout_s: float = 600.0

    # JWT
    jwt_secret_key: str = _DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7

    # SAML SSO / 프론트엔드 (네이버웍스 로그인 리다이렉트)
    saml_base_url: str = ""  # 운영 공개 베이스 URL (비면 요청 헤더로 추론)
    # SSO 사용 여부 — 끄면 로그인 화면에서 버튼이 사라지고 SAML 엔드포인트도 막힌다.
    # IdP 값을 넣기 전(또는 IdP 점검 중)에 버튼만 살아 있으면 사용자는 오류만 본다.
    sso_enabled: bool = True
    # SAML IdP(네이버웍스) — 관리자 설정에서 덮어쓸 수 있다(app_settings). 여기 값은
    # .env 미설정 시 기본값이다. IdP를 바꾸려고 재배포하지 않아도 되게 하려는 것
    # (2026-08-10: 코드에 하드코딩돼 있어 변경마다 재배포가 필요했다).
    saml_idp_entity_id: str = "https://auth.worksmobile.com/saml2/oseop.by-works.net"
    saml_idp_sso_url: str = "https://auth.worksmobile.com/saml2/idp/oseop.by-works.net"
    saml_idp_x509cert: str = "MIIC6jCCAdKgAwIBAgIIX0B8jtiXKtYwDQYJKoZIhvcNAQELBQAwNTETMBEGA1UEAwwKTElORSBXT1JLUzERMA8GA1UEBwwIU0VPTkdOQU0xCzAJBgNVBAYTAktSMB4XDTI2MDUzMDEyMzc0N1oXDTMxMDUzMDEyMzc0N1owNTETMBEGA1UEAwwKTElORSBXT1JLUzERMA8GA1UEBwwIU0VPTkdOQU0xCzAJBgNVBAYTAktSMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAvnAOMLaXoeEto9PryxQVpqt0FrlWpTbIQDcCenCbI70F2HMkAh5twBCPY6Mv5NLNYFDJF4NselJcMYCFwtF2otUGLINzUXtAVrksRMsvIjHmh7ldQRvyK7k/WJLHdSX3qEyJre6sdvlWWshA+nX51vS2x5XR8r/KXYN6OTKgtTyBYaRvPO58hNUvXC8ZY0sss2zWdiFweuprkxI6wF8TQDSKWf02vi26nRNMsfcigK12QRNcni1sVPUEdiDbxfhBON0GgInXeVU+Oqd6cMC8bjnHaA7o6loVGlk17V+2l2cidZEhI7bkJAoY7yxKjDJERB1fZ45TipTyrtz6rhSbrwIDAQABMA0GCSqGSIb3DQEBCwUAA4IBAQBljuK68BXDAg/SrP8cmgK0Rlwh0nYg21M/7pNj5T+bDDuyWZzsw9djWmIlXjzr8kALiz+miUtDRIBNoyANi68Ed1NlIDXa4yP++IoJDdMeAF9YScPsQgEX23+CY1sxHKhoTUuznvFdAqmkSJ/uhJMXkMliZtQdShRQcK3pVoZ9NYTIy2GXIlXN9W17rAd/EfR0DV4AFUYbvanrUPrRLcqn3LEn1414W6AQBk0atCL4Y3ZYZQfyeIwCf2oqBqHmfQxkkSwaGjbP2FuczIGrFvXb7BI311feFQyPN5BASWMtPjiTtL+Kgf8AjTho/Xrw9wRsrX5jD10+EmiFp16eBaED"  # noqa: E501
    react_frontend_url: str = "http://localhost:5173"

    # 관리자 — 조직 월 비용 한도(USD). quota_settings(ORG_MONTHLY_COST_LIMIT_USD) 행이
    # 아직 없을 때(마이그레이션 미실행 등)의 폴백 하한값 — 정상 운영 중에는 DB 값을
    # 우선한다(src.services.quota_settings.get_quota_setting_int).
    # (사용자별 한도는 user_quotas 테이블, 역할별 기본값은 src.core.limit)
    org_monthly_cost_limit_usd: Decimal = Decimal("3000")
    # 한도 enforcement — 실호출(live/record) 전 당월 누적 비용 vs 한도 검사. 끄면 표시만.
    quota_enforcement_enabled: bool = True
    # X-API-Key
    internal_api_key: str = ""
    # app_settings 시크릿(API키·네이버웍스 키 등) 대칭 암호화 키(Fernet, urlsafe-base64 32B).
    # 비우면 jwt_secret_key에서 파생(하위 호환)하지만, 그러면 JWT 키를 로테이션할 때 저장된
    # 시크릿이 복호화 불가해진다. 운영은 `Fernet.generate_key()`로 뽑은 전용 키를 넣을 것.
    secrets_encryption_key: str = ""

    # NAVER WORKS API
    # 파이프라인 이벤트(게이트 도달·완료·실패) 시 소유자 봇 알림. 로컬에서 자격증명이
    # 더미면 NOTIFY_ENABLED=false로 끄면 경고 로그가 안 쌓인다.
    notify_enabled: bool = True
    # 네이버웍스는 부가 기능(SSO·봇 알림)이라 자격증명이 없어도 부팅되게 optional 로 둔다.
    # 값은 실제 NW 호출(토큰 발급·봇 전송·SAML) 시점에만 지연 소비되므로, 안 쓰면 비워도
    # 무방하다. 알림을 끄려면 NOTIFY_ENABLED=false 로 둘 것.
    nw_client_id: str = ""
    nw_client_secret: str = ""
    nw_service_account: str = ""
    nw_private_key: str = ""
    nw_bot_id: str = ""
    nw_token_expire_sec: int = 3600
    nw_refresh_buffer: int = 60

    # 임베딩 (환경별 변동 가능 — 모델 특성 상수는 클라이언트 ClassVar로 관리)
    embedding_model_path: str = "./models/bge-m3-onnx-int8"
    embedding_cache_dir: str = "./cache/embeddings"

    # 청킹 (튜닝 가능 — 길이 범위·헤더 레벨 등 라이브러리 상수는 서비스 ClassVar)
    chunking_breakpoint_amount: int = 95

    # 리랭커 (어댑터 내부 동작 상수는 ClassVar, 운영 토글·경로만 환경 변수)
    reranker_model_path: str = "./models/bge-reranker-v2-m3-onnx-int8"
    # 색인 임베딩 배치 — 한 자료의 청크를 통째로 넣으면 ONNX가 배치 안 최장 시퀀스에
    # 맞춰 전부 패딩해 중간 텐서가 폭증한다(실측 14GB). 배치를 끊으면 상한이
    # batch × max_len로 묶인다. 처리량 손해는 미미하다(같은 총 토큰을 나눠 넣을 뿐).
    embedding_batch_size: int = 16
    reranker_batch_size: int = 16
    reranker_max_length: int = 512
    reranker_enabled: bool = True

    # 원격 GPU 리랭커 — url이 비면 로컬 CPU 모델(기본). 채우면 그 주소로 재채점을 보낸다.
    # 점수는 순위 결정에만 쓰이고 저장되지 않아 벡터 공간 일관성 문제가 없다 → 원격화 1순위.
    reranker_remote_url: str = ""
    reranker_remote_token: str = ""
    # 후보 96개 재채점이 3060 Ti에서 2~4초 예상. 60초면 CPU 폴백보다 훨씬 빨리 포기한다.
    reranker_remote_timeout_s: float = 60.0
    reranker_remote_connect_timeout_s: float = 5.0
    # 원격이 죽었을 때 매 절마다 타임아웃을 다시 기다리지 않도록 잠시 끈다
    # (20절 × 60초 = 20분을 죽은 서비스에 버리는 사고 방지).
    reranker_remote_cooldown_s: float = 60.0
    # local = 서버 CPU 모델 재채점(품질 동일·서버 메모리 필요)
    # passthrough = 재채점 생략, 검색 순위 통과(품질 소폭 하락·서버에서 모델 제거 가능)
    reranker_remote_fallback: str = "local"

    # 원격 GPU 임베딩 — url이 비면 로컬 CPU 모델(기본). 리랭커와 성격이 다르다:
    # 벡터가 **색인에 저장되므로** 원격/로컬의 dtype이 다르면 질의 벡터가 색인과
    # 다른 공간에 놓여 검색이 조용히 나빠진다. 전량 재색인으로 공간을 통일한 뒤에만
    # 켤 것(2026-08-20 결정: 초기 단계라 재색인 허용).
    # 교차언어 대목 정렬(services/qa/dense_align) — 어휘가 원리적으로 0점을 주는 구간을
    # 다국어 임베딩으로 다시 본다. **2026-08-27부터 기본 on.**
    #
    # 무엇을 사나: 확정(aligned) 비율 20.9% → 27.5%, 교차언어 문장의 18.8%가 대목을
    # 찾는다(실측: 절 116개·문장 10,448개). 어휘 겹침으로는 원리적으로 못 하는 일이다.
    #
    # 무엇을 내나: 대목 벡터를 색인 때 저장하므로(0050) 남는 건 주장 임베딩과 행렬곱뿐
    # 이라 교차언어가 많은 절이 0.4~1.7초, 대부분은 그 아래다. 백필 전 청크는 그 자리에서
    # 대목을 만들어 1.8~3.3초 - 느릴 뿐 결과는 같다(실측으로 대목·승격 전부 일치).
    dense_align_enabled: bool = True

    # 근거 주입 형식 - "plain"(현행: "[n](라벨) 본문" 평문 줄) | "tagged"(경계·번호를
    # 태그로 명시). 오귀속(내용은 맞고 번호가 틀림)이 무근거 수치 문장의 35.1%라
    # (2026-08-27 실측, 220절) 주입 구조가 원인인지 A/B로 잰다. JSON이 아니라 태그인
    # 이유: 한국어 장문을 JSON 문자열로 넣으면 이스케이프 노이즈에 토큰만 늘고,
    # 번호가 본문과 물리적으로 붙는 것이 오귀속에 필요한 성질이다.
    evidence_injection_format: str = "plain"

    embedding_remote_url: str = ""
    embedding_remote_token: str = ""
    # 색인은 요청이 수천 건이라 개별 요청은 짧아야 한다. 512건 배치가 GPU에서 수 초다.
    embedding_remote_timeout_s: float = 60.0
    embedding_remote_connect_timeout_s: float = 5.0
    # 리랭커보다 중요하다 - 색인 한 번에 수천 번 호출하므로 쿨다운이 없으면 죽은
    # 서비스를 상대로 타임아웃을 수천 번 기다린다.
    embedding_remote_cooldown_s: float = 60.0
    # 한 요청에 보낼 텍스트 수. GPU 서비스의 GPU_MAX_PASSAGES(기본 512) 이하여야 한다.
    embedding_remote_chunk: int = 256
    # passthrough는 없다 - 벡터 생성은 건너뛸 수 없다. local(서버 CPU 모델) 아니면
    # error(요청 실패로 드러냄)뿐이다. dtype 불일치를 조용히 만들지 않으려면 error가
    # 안전하지만, 사용자 결정은 "GPU 우선·CPU 폴백 유지"라 기본은 local이다.
    embedding_remote_fallback: str = "local"

    # 원격 GPU 파싱(docling) — url이 비면 로컬 docling(기본). 동기는 속도보다 품질이다:
    # 앱 서버에서 docling이 메모리 부족으로 죽으면 pymupdf로 **조용히** 떨어져 표
    # 구조가 사라진다(2026-08-20 실사고: 13건 중 9건). 전용 GPU 박스로 옮기면 그
    # 폴백 계급 자체가 없어진다.
    parser_remote_url: str = ""
    parser_remote_token: str = ""
    # 파싱은 건당 수 분이다(12.5MB/368p가 GPU에서 234초 실측). read 타임아웃이 곧
    # 총 예산이다 - 서버 데드라인(570초)보다 길어야 깔끔한 504를 받는다.
    parser_remote_timeout_s: float = 600.0
    parser_remote_connect_timeout_s: float = 5.0
    # 리랭커(60초)보다 훨씬 길다 - 실패 1회의 비용이 최대 600초라, 죽은 서비스에
    # 문서마다 10분씩 버리면 업로드 대기열이 통째로 굳는다.
    parser_remote_cooldown_s: float = 300.0
    # 이 크기 미만만 원격으로 보낸다. 25MB인 이유: 17MB 표 밀집 PDF가 GPU에서도
    # 15분을 넘긴 실측이 있다 - 더 큰 파일은 600초를 태우고 실패할 공산이 크다.
    # 운영 실측(13~25MB 구간 파싱 시간) 후 올린다. 초과분은 기존처럼 pymupdf.
    parser_remote_max_bytes: int = 25 * 1024 * 1024
    # local = 앱 서버 로컬 docling(품질 동일·느림·메모리 위험은 색인 직렬화로 완화)
    # pymupdf = 로컬 docling 생략(앱 서버를 줄인 배포용 - 표 품질 손해는 이제 기록됨)
    parser_remote_fallback: str = "local"

    # 자료 라이브러리 — 업로드 파일 저장 위치
    library_dir: str = "./data/library"

    # 산출물(HWPX) — 완료 보고서 파일 출력
    export_dir: str = "./exports"
    export_template_path: str = ""  # 한컴 마스터 템플릿(.hwpx). 비면 빈 문서 + 코드 서식

    # HyDE 쿼리 확장 (dense 검색 전용) — 기본 off. 효과는 eval_search(nDCG)로 측정 후 결정
    hyde_enabled: bool = False
    hyde_model: str = "gemini-3.1-flash-lite"  # 실작동 최저가(2026-08-04 실측 승계)

    # MMR — 리랭킹 결과에서 상위 k를 '관련성 - 이미 뽑은 것과의 중복'으로 고른다.
    # **기본 off. 켜기 전에 top_k부터 손봐야 한다**(2026-08-12 실측).
    #   하한 없이 켜면: 고유 청크 194→218로 늘지만 선택된 청크 점수의 하위 10%가
    #     0.011→0.001로 무너진다 - 다양화가 리랭커가 '관련 없다'고 본 것을 끌어온다.
    #   하한을 걸면: 통과 후보가 top_k에 못 미쳐 폴백이 발동해 아무 일도 안 한다.
    #     후보 64개 중 0.05점 이상이 32개가 안 되기 때문이다(절당 ≥0.5는 중앙 15개).
    # 즉 문제는 다양성이 아니라 top_k=32가 리랭커 확신 구간을 한참 넘는 것이다.
    # 켜 두면 '켰는데 효과 없다'는 잘못된 결론만 남으므로 off로 둔다.
    mmr_enabled: bool = False
    mmr_lambda: float = 0.75
    # 이 점수 아래는 다양화 후보에서 뺀다. 고정값은 질의별 점수 스케일 흔들림에 약하다 -
    # 되살릴 때 '상위 1위의 x%'처럼 분포 기반으로 바꿀 것(재현성을 위해 방식은 상수로).
    mmr_min_score: float = 0.05

    # 다국어 검색 — 한글 질의로 dense 상위 20에 영문 청크가 0건이었다(2026-08-11 실측.
    # 같은 뜻의 영문 질의로는 6~12건). 영문 자료를 모아 색인해 놓고 안 쓰는 상태라
    # 질의를 번역해 한 번 더 검색하고 순위를 합친다(색인은 그대로 — 원문 대조 유지).
    multilingual_search_enabled: bool = True
    # 근거 풀에 한글 없는 청크가 이 비율 미만이면 번역 콜 자체를 걸지 않는다.
    # 국내 자료만 있는 프로젝트가 대다수라 무조건 켜면 절마다 헛콜이 붙는다.
    multilingual_min_foreign_ratio: float = 0.05
    multilingual_query_model: str = "gemini-3.1-flash-lite"  # 실작동 최저가

    # RAPTOR 요약 트리 — 인덱싱 후 의미 클러스터링 요약을 쌓아 검색 맥락으로 제공.
    # 트리 깊이는 depth_mode가 결정(indexing/raptor.DEPTH_LEVELS). 빌드 실패는 비치명.
    raptor_enabled: bool = True
    raptor_model: str = "gemini-3.1-flash-lite"  # 실작동 최저가(2026-08-04 실측 승계)
    # 약어 사전 설명 생성(assemble 1콜) — 배경 지원 기능이라 최저가 모델
    glossary_model: str = "gemini-3.1-flash-lite"

    # 자료 용어 채굴·주입 — 색인 때 문서가 정의한 용어·한영 병기를 자료 행에 적립하고
    # (indexing/terms), 작성 시 근거팩에 등장하는 것만 프롬프트에 싣는다(generation/
    # term_rules). 청킹이 정의와 사용처를 갈라놓아 생기는 오역의 처방(2026-08-27).
    term_mining_llm: bool = True  # 패턴 채굴에 LLM 보강 1콜(문서당·해시 멱등)을 얹을지
    term_mining_model: str = "gemini-3.1-flash-lite"  # 실작동 최저가 계보 승계
    term_injection: bool = True  # 작성·재작성 프롬프트에 용어 규칙 블록 주입
    # 클러스터 요약 동시 실행 수 — 순차로 돌리면 224콜(예타 런 L1 192+L2 32)을 줄
    # 세워 기다려 인덱싱에 10~15분이 쌓인다(2026-08-10 실측). DB 커넥션 풀(기본
    # 5+10)과 API 한도를 고려해 8.
    raptor_summary_concurrency: int = 8
    raptor_top_k: int = 3  # 섹션 검색에 곁들일 배경 맥락 요약 수(상한 — 하한은 유사도)
    # 작성 프롬프트에 RAPTOR 요약을 배경 맥락으로 실을지 — A/B의 A팔(주입)이다.
    # 기본 off(B팔): +18~20% 인용 실측은 질의가 장끼리 같고 top_k=32이던 조건이라,
    # leaf 대표성이 낮을 때 배경이 메운 효과일 가능성이 있다(2026-08-15 합의). 리허설
    # 도입 후 A/B로 재판정한다 — 되돌리기는 이 플래그 하나다. 트리 구축과 리허설의
    # 클러스터 대조는 이 플래그와 무관하게 유지된다.
    raptor_write_inject: bool = False
    # 요약 채택의 코사인 유사도 하한 — top_k 고정 3은 관련 없는 요약도 억지로 채웠다.
    # 하한을 넘는 것만 0~top_k개 가변 채택(leaf의 상대 하한과 같은 철학, 절대값인
    # 이유는 요약 검색이 단일 코사인 축이라 스케일이 안정적이기 때문).
    # 0.55 = 검증런 트리(153노드) 실측 캘리브레이션(2026-08-15): 실제 절 질의
    # 0.64~0.75 / 인접 주제지만 자료 없음 0.44 / 완전 무관 0.31~0.34. 처음 잡았던
    # 0.35는 인접-무자료(0.44)도 통과시켜 리허설의 raptor_gap 판정이 항상 False가
    # 되는 무력한 값이었다. 트리 1개 기준이라 다른 도메인 런에서 재확인할 것.
    raptor_min_similarity: float = 0.55

    # PM 검증 — assemble 직후 챕터당 1콜, 문서 횡단 일관성 경고 리포트(차단 아님).
    pm_verify_enabled: bool = True
    verify_model: str = "claude-sonnet-4-6"  # 테스트 시 .env로 haiku 교체
    # 근거 동봉 판정 — 어휘 겹침이 건져 올린 의심 문장만 LLM에 근거와 함께 넘겨
    # 뒷받침 여부를 확인한다(의역 오탐 제거). 끄면 겹침 판정만으로 경고한다.
    claim_verify_enabled: bool = True
    # 판정은 "이 근거가 이 문장을 담고 있나"만 묻는 짧은 일이라 상위 모델이 필요 없다.
    # 실측(2026-08-11, 의역·창작수치·의미반전·과잉단정 4종): flash-lite와 Haiku 4.5가
    # 똑같이 4/4. 단가는 1/4($0.25 vs $1 입력)라 기본을 이쪽에 둔다.
    claim_verify_model: str = "gemini-3.1-flash-lite"

    @property
    def is_production(self) -> bool:
        return self.environment == Environment.PRODUCTION

    @property
    def is_local(self) -> bool:
        return self.environment == Environment.LOCAL

    @property
    def cors_origins(self) -> list[str]:
        if self.environment == Environment.LOCAL:
            return ["*"]
        return [
            "https://www.rowninsight.cloud",
            "https://rowninsight.cloud",
        ]

    @property
    def nw_private_key_pem(self) -> str:
        return self.nw_private_key.replace("\\n", "\n")

    @model_validator(mode="after")
    def _guard_production_secrets(self) -> Self:
        """
        운영 환경에서 안전하지 않은 JWT 키면 부팅을 차단한다 (fail-fast).

        .env/OS 환경변수로 키 주입을 깜빡했을 때 기본값으로 조용히 뜨는 사고를 방지.
        """
        if self.is_production and (
            self.jwt_secret_key == _DEFAULT_JWT_SECRET or len(self.jwt_secret_key) < 32
        ):
            raise ValueError(
                "운영 환경(production)에서 JWT_SECRET_KEY가 기본값이거나 32자 미만입니다. "
                "안전한 무작위 키(32자 이상)를 환경변수로 주입하세요."
            )
        return self


settings = Settings()  # type: ignore[call-arg]
