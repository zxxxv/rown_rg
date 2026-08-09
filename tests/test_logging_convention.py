import json
import logging

import pytest
import structlog

from src.core.logging import configure_logging


@pytest.fixture
def production_logging(monkeypatch, capsys):
    monkeypatch.setattr("src.core.config.settings.environment", "production")
    configure_logging()
    yield capsys
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    logging.getLogger().handlers.clear()


class TestLoggingConvention:
    def test_production_logs_are_valid_json(self, production_logging):
        log = structlog.get_logger()
        log.info("test.event", foo="bar", count=42)

        out = production_logging.readouterr().out
        parsed = json.loads(out.strip())
        assert parsed["event"] == "test.event"
        assert parsed["foo"] == "bar"
        assert parsed["count"] == 42
        assert "timestamp" in parsed
        assert parsed["level"] == "info"

    def test_stdlib_logger_also_routes_through_structlog(self, production_logging):
        stdlib_log = logging.getLogger("external_lib_simulation")
        stdlib_log.warning("external warning")

        out = production_logging.readouterr().out
        parsed = json.loads(out.strip())
        assert parsed["level"] == "warning"

    def test_contextvar_appears_in_log(self, production_logging):
        structlog.contextvars.bind_contextvars(request_id="abc-123")
        log = structlog.get_logger()
        log.info("request.handled")

        out = production_logging.readouterr().out
        parsed = json.loads(out.strip())
        assert parsed["request_id"] == "abc-123"

    def test_exception_traceback_in_json(self, production_logging):
        log = structlog.get_logger()
        try:
            raise ValueError("test error")
        except ValueError:
            log.exception("test.failed")

        out = production_logging.readouterr().out
        parsed = json.loads(out.strip())
        assert parsed["event"] == "test.failed"
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]
