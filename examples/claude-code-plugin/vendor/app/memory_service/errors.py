class MemoryServiceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class InvalidScopeError(MemoryServiceError):
    def __init__(self, message: str):
        super().__init__("invalid_scope", message)


class InvalidRequestError(MemoryServiceError):
    def __init__(self, message: str):
        super().__init__("invalid_request", message)


class NotFoundError(MemoryServiceError):
    def __init__(self, message: str):
        super().__init__("not_found", message)


class IndexUnavailableError(MemoryServiceError):
    def __init__(self, message: str):
        super().__init__("index_unavailable", message)


class IntegrationFailedError(MemoryServiceError):
    def __init__(self, message: str):
        super().__init__("integration_failed", message)


class ConsolidationUnavailableError(MemoryServiceError):
    def __init__(self, message: str):
        super().__init__("consolidation_unavailable", message)


class ReviewUnavailableError(MemoryServiceError):
    def __init__(self, message: str):
        super().__init__("review_unavailable", message)
