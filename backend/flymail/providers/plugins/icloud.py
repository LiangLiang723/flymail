"""iCloud Mail provider capability data."""

from flymail.providers.contracts import (
    ProviderCapabilities,
    ProviderEndpoints,
    SentCopyStrategy,
    ServiceEndpoint,
    TransportSecurity,
)
from flymail.providers.plugins.base import StaticProviderPlugin


PLUGIN = StaticProviderPlugin(
    key="icloud",
    capability_set=ProviderCapabilities(
        supports_idle=False,
        supports_move=False,
        supports_uidplus=False,
        supports_condstore=False,
        supports_qresync=False,
        supports_gmail_labels=False,
        supports_special_use=False,
        supports_smtp_utf8=False,
        supports_oauth=False,
        auto_saves_sent_copy=False,
        max_parallel_connections=2,
        recommended_poll_seconds=60,
        idle_refresh_seconds=120,
        max_fetch_batch=50,
        max_attachment_bytes=15 * 1024 * 1024,
    ),
    endpoint_set=ProviderEndpoints(
        imap=ServiceEndpoint("imap.mail.me.com", 993, TransportSecurity.TLS),
        smtp=ServiceEndpoint("smtp.mail.me.com", 587, TransportSecurity.STARTTLS),
    ),
    sent_strategy=SentCopyStrategy.IMAP_APPEND,
)
