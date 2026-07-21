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


class DatabaseError(BaseError):
    pass
