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


class DatabaseError(BaseError):
    pass


class CostLimitExceededError(BaseError):
    pass
