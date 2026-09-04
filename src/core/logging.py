import logging
import os
import sys
from logging.handlers import RotatingFileHandler

import sentry_sdk
import structlog

from src.core.config import settings


class _StdoutHandler(logging.StreamHandler):
    """StreamHandler that resolves sys.stdout at emit time.

    Why: pytest's capsys patches sys.stdout per-test; a handler bound to
    sys.stdout at construction time would write to a stale buffer.
    """

    @property
    def stream(self):
        return sys.stdout

    @stream.setter
    def stream(self, _):
        pass


def configure_logging() -> None:
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.is_production:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = _StdoutHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # 프로덕션: stdout에 더해 파일로도 JSON 로그를 순환 기록한다. 컨테이너를 재생성해도
    # (배포) 로그가 사라지지 않게 /app/logs(영속 볼륨)에 남긴다 — 2026-08-12 실측: 배포마다
    # 로그가 소실돼 과거 이벤트(예: docling 파싱) 조회가 불가능했다. stdout 핸들러는 그대로
    # 두어 `docker compose logs`도 계속 동작한다. 파일 로깅 실패는 비치명(경고만).
    if settings.is_production:
        log_dir = "/app/logs"
        try:
            os.makedirs(log_dir, exist_ok=True)
            file_handler = RotatingFileHandler(
                os.path.join(log_dir, "app.log"),
                maxBytes=50 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)
        except OSError:
            root_logger.warning("file_logging.setup_failed", exc_info=True)
            # 파일 로깅만 못 붙는다 — stdout 핸들러는 그대로라 비치명. 다만 배포
            # 후 로그가 다시 휘발되는 상태이므로 알린다(프로세스당 1회).
            with sentry_sdk.new_scope() as scope:
                scope.set_tag("bg_failure", "file_logging_setup")
                scope.set_extra("log_dir", log_dir)
                sentry_sdk.capture_exception()

    for noisy in ("uvicorn.access", "httpx", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
