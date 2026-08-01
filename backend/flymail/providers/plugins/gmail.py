"""Gmail provider capability data and mailbox aliases."""

from flymail.providers.contracts import (
    ProviderCapabilities,
    ProviderEndpoints,
    SentCopyStrategy,
    ServiceEndpoint,
    TransportSecurity,
)
from flymail.providers.plugins.base import StaticProviderPlugin


PLUGIN = StaticProviderPlugin(
    key="gmail",
    capability_set=ProviderCapabilities(
        supports_idle=True,
        supports_move=False,
        supports_uidplus=False,
        supports_condstore=False,
        supports_qresync=False,
        supports_gmail_labels=True,
        supports_special_use=True,
        supports_smtp_utf8=False,
        supports_oauth=True,
        auto_saves_sent_copy=True,
        max_parallel_connections=3,
        recommended_poll_seconds=300,
        idle_refresh_seconds=120,
        max_fetch_batch=100,
        max_attachment_bytes=18 * 1024 * 1024,
    ),
    endpoint_set=ProviderEndpoints(
        imap=ServiceEndpoint("imap.gmail.com", 993, TransportSecurity.TLS),
        smtp=ServiceEndpoint("smtp.gmail.com", 587, TransportSecurity.STARTTLS),
    ),
    sent_strategy=SentCopyStrategy.PROVIDER_AUTO,
    mailbox_aliases=(
        ("[gmail]/sent mail", "sent"),
        ("[google mail]/sent mail", "sent"),
        ("[gmail]/drafts", "drafts"),
        ("[google mail]/drafts", "drafts"),
        ("[gmail]/spam", "junk"),
        ("[google mail]/spam", "junk"),
        ("[gmail]/trash", "trash"),
        ("[google mail]/trash", "trash"),
        ("[gmail]/all mail", "all_mail"),
        ("[google mail]/all mail", "all_mail"),
        ("[gmail]/important", "important"),
        ("[google mail]/important", "important"),
    ),
)
