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


class AuthenticationError(FlyMailError):
    """Raised when no valid local session is available."""


class InvalidCredentialsError(AuthenticationError):
    """Raised for indistinguishable local-login credential failures."""


class CsrfError(AuthorizationError):
    """Raised when an unsafe cookie-authenticated request fails CSRF checks."""


class RateLimitError(FlyMailError):
    """Raised when an authentication principal/source window is blocked."""


class UnsafeEndpointError(FlyMailError):
    """Raised when a user-supplied network endpoint is not publicly routable."""


class UnsupportedProviderError(FlyMailError):
    """Raised when an account references a provider plugin that is not registered."""


class RetryableError(FlyMailError):
    """Raised for a failure that may succeed after retry or backoff."""


class PermanentError(FlyMailError):
    """Raised for a failure that must not be retried automatically."""
