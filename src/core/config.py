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

    # JWT
    jwt_secret_key: str = _DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7

    # SAML SSO / 프론트엔드 (네이버웍스 로그인 리다이렉트)
    saml_base_url: str = ""  # 운영 공개 베이스 URL (비면 요청 헤더로 추론)
    react_frontend_url: str = "http://localhost:5173"

    # 관리자 — 조직 월 비용 한도(USD). 관리자 대시보드 KPI 표시·예산 경고용.
    # (사용자별 한도는 user_quotas 테이블, 역할별 기본값은 src.core.limit)
    org_monthly_cost_limit_usd: Decimal = Decimal("3000")
    # 한도 enforcement — 실호출(live/record) 전 당월 누적 비용 vs 한도 검사. 끄면 표시만.
    quota_enforcement_enabled: bool = True
    # X-API-Key
    internal_api_key: str = ""

    # NAVER WORKS API
    nw_client_id: str
    nw_client_secret: str
    nw_service_account: str
    nw_private_key: str
    nw_bot_id: str
    nw_token_expire_sec: int = 3600
    nw_refresh_buffer: int = 60

    # 임베딩 (환경별 변동 가능 — 모델 특성 상수는 클라이언트 ClassVar로 관리)
    embedding_model_path: str = "./models/bge-m3-onnx-int8"
    embedding_cache_dir: str = "./cache/embeddings"

    # 청킹 (튜닝 가능 — 길이 범위·헤더 레벨 등 라이브러리 상수는 서비스 ClassVar)
    chunking_breakpoint_amount: int = 95

    # 리랭커 (어댑터 내부 동작 상수는 ClassVar, 운영 토글·경로만 환경 변수)
    reranker_model_path: str = "./models/bge-reranker-v2-m3-onnx-int8"
    reranker_batch_size: int = 16
    reranker_max_length: int = 512
    reranker_enabled: bool = True

    # 산출물(HWPX) — 완료 보고서 파일 출력
    export_dir: str = "./exports"
    export_template_path: str = ""  # 한컴 마스터 템플릿(.hwpx). 비면 빈 문서 + 코드 서식

    # HyDE 쿼리 확장 (dense 검색 전용) — 기본 off. 효과는 eval_search(nDCG)로 측정 후 결정
    hyde_enabled: bool = False
    hyde_model: str = "gemini-2.5-flash"

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
        return ["https://app.loune-insight.co.kr"]

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
