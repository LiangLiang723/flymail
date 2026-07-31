"""FlyMail V2 domain and infrastructure error types."""


class FlyMailError(Exception):
    """Base class for expected FlyMail V2 failures."""


class ConfigurationError(FlyMailError, ValueError):
    """Raised when required runtime configuration is invalid."""


class NotFoundError(FlyMailError):
    """Raised when an authorized resource cannot be found."""


class ConflictError(FlyMailError):
    """Raised when observed state conflicts with the requested change."""


class AuthorizationError(FlyMailError):
    """Raised when the current principal cannot perform an operation."""


class RetryableError(FlyMailError):
    """Raised for a failure that may succeed after retry or backoff."""


class PermanentError(FlyMailError):
    """Raised for a failure that must not be retried automatically."""
