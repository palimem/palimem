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


class ProfileUnavailableError(MemoryServiceError):
    def __init__(self, message: str):
        super().__init__("profile_unavailable", message)


class ReflectionUnavailableError(MemoryServiceError):
    def __init__(self, message: str):
        super().__init__("reflection_unavailable", message)


class ExtractionDisabledError(MemoryServiceError):
    def __init__(self, message: str):
        super().__init__("extraction_disabled", message)


class PiiBlockedError(MemoryServiceError):
    def __init__(self, message: str):
        super().__init__("pii_blocked", message)


class LegalHoldError(MemoryServiceError):
    def __init__(self, message: str):
        super().__init__("legal_hold", message)


class TemporalQueryUnavailableError(MemoryServiceError):
    def __init__(self, message: str):
        super().__init__("temporal_query_unavailable", message)


class AuditExportUnavailableError(MemoryServiceError):
    def __init__(self, message: str):
        super().__init__("audit_export_unavailable", message)


class FleetSyncUnavailableError(MemoryServiceError):
    def __init__(self, message: str):
        super().__init__("fleet_sync_unavailable", message)
