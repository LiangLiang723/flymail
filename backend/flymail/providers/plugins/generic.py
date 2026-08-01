"""Conservative user-supplied IMAP/SMTP provider."""

from flymail.providers.contracts import (
    ProviderCapabilities,
    ProviderEndpoints,
    SentCopyStrategy,
)
from flymail.providers.plugins.base import StaticProviderPlugin


PLUGIN = StaticProviderPlugin(
    key="generic",
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
        max_attachment_bytes=20 * 1024 * 1024,
    ),
    endpoint_set=ProviderEndpoints(
        imap=None,
        smtp=None,
        user_supplied=True,
    ),
    sent_strategy=SentCopyStrategy.IMAP_APPEND,
)
