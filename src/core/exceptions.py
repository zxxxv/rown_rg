class BaseError(Exception):
    def __init__(self, message: str = "", code: str = ""):
        self.message = message
        self.code = code
        super().__init__(self.message)


class AuthenticationError(BaseError):
    pass


class AuthorizationError(BaseError):
    pass


class NotFoundError(BaseError):
    pass


class ValidationError(BaseError):
    pass


class LLMError(BaseError):
    pass


class QuotaExceededError(BaseError):
    """월 비용 한도(사용자/조직) 초과로 LLM 호출이 차단됨 → HTTP 429."""


class IncompleteReportError(BaseError):
    """조립 시점에 미작성(빈) 절이 남아 완성으로 마감할 수 없음.

    write의 절 단위 실패는 실행을 죽이지 않고 여기서 한 번에 표면화된다 — 빈 절을
    실은 보고서가 status=completed로 둔갑한 실사고(2026-08-13) 재발 방지 게이트.
    """


class DatabaseError(BaseError):
    pass


class CostLimitExceededError(BaseError):
    pass
