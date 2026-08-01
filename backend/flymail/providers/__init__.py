"""Provider-neutral protocol contracts for FlyMail V2."""

from flymail.providers.contracts import (
    EndpointVariant,
    MailboxMapping,
    ProviderCapabilities,
    ProviderEndpoints,
    ProviderPlugin,
    SentCopyStrategy,
    ServiceEndpoint,
    TransportSecurity,
)
from flymail.providers.errors import ProviderError, ProviderErrorCode
from flymail.providers.registry import ProviderRegistry

__all__ = [
    "EndpointVariant",
    "MailboxMapping",
    "ProviderCapabilities",
    "ProviderEndpoints",
    "ProviderError",
    "ProviderErrorCode",
    "ProviderPlugin",
    "ProviderRegistry",
    "SentCopyStrategy",
    "ServiceEndpoint",
    "TransportSecurity",
]
