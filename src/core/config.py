from enum import StrEnum

from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
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

    # JWT
    jwt_secret_key: str = "change-me-32-chars-or-more"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7

    # 임베딩 (환경별 변동 가능 — 모델 특성 상수는 클라이언트 ClassVar로 관리)
    embedding_model_path: str = "./models/bge-m3-onnx-int8"
    embedding_cache_dir: str = "./cache/embeddings"

    @property
    def is_production(self) -> bool:
        return self.environment == Environment.PRODUCTION

    @property
    def cors_origins(self) -> list[str]:
        if self.environment == Environment.LOCAL:
            return ["*"]
        return ["https://app.loune-insight.co.kr"]


settings = Settings()
