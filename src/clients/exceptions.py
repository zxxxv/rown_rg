from src.core.exceptions import LLMError


class LLMClientError(LLMError):
    pass


class CassetteNotFoundError(LLMClientError):
    pass


class LLMRateLimitError(LLMClientError):
    pass


class LLMAPIError(LLMClientError):
    pass
