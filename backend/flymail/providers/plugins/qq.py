"""QQ Mail provider capability data."""

from flymail.providers.contracts import (
    ProviderCapabilities,
    ProviderEndpoints,
    SentCopyStrategy,
    ServiceEndpoint,
    TransportSecurity,
)
from flymail.providers.plugins.base import StaticProviderPlugin


PLUGIN = StaticProviderPlugin(
    key="qq",
    capability_set=ProviderCapabilities(
        supports_idle=True,
        supports_move=False,
        supports_uidplus=False,
        supports_condstore=False,
        supports_qresync=False,
        supports_gmail_labels=False,
        supports_special_use=False,
        supports_smtp_utf8=False,
        supports_oauth=False,
        auto_saves_sent_copy=False,
        max_parallel_connections=3,
        recommended_poll_seconds=300,
        idle_refresh_seconds=120,
        max_fetch_batch=100,
        max_attachment_bytes=35 * 1024 * 1024,
    ),
    endpoint_set=ProviderEndpoints(
        imap=ServiceEndpoint("imap.qq.com", 993, TransportSecurity.TLS),
        smtp=ServiceEndpoint("smtp.qq.com", 465, TransportSecurity.TLS),
    ),
    sent_strategy=SentCopyStrategy.IMAP_APPEND,
)
