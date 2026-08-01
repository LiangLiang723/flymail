"""NetEase Mail provider capability data and domain endpoint variants."""

from flymail.providers.contracts import (
    EndpointVariant,
    ProviderCapabilities,
    ProviderEndpoints,
    SentCopyStrategy,
    ServiceEndpoint,
    TransportSecurity,
)
from flymail.providers.plugins.base import StaticProviderPlugin


PLUGIN = StaticProviderPlugin(
    key="netease",
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
        max_attachment_bytes=35 * 1024 * 1024,
    ),
    endpoint_set=ProviderEndpoints(
        imap=ServiceEndpoint("imap.163.com", 993, TransportSecurity.TLS),
        smtp=ServiceEndpoint("smtp.163.com", 465, TransportSecurity.TLS),
        variants=(
            EndpointVariant(
                domain_suffixes=("@126.com",),
                imap=ServiceEndpoint("imap.126.com", 993, TransportSecurity.TLS),
                smtp=ServiceEndpoint("smtp.126.com", 465, TransportSecurity.TLS),
            ),
            EndpointVariant(
                domain_suffixes=("@188.com",),
                imap=ServiceEndpoint("imap.188.com", 993, TransportSecurity.TLS),
                smtp=ServiceEndpoint("smtp.188.com", 465, TransportSecurity.TLS),
            ),
            EndpointVariant(
                domain_suffixes=("@yeah.net",),
                imap=ServiceEndpoint("imap.yeah.net", 993, TransportSecurity.TLS),
                smtp=ServiceEndpoint("smtp.yeah.net", 465, TransportSecurity.TLS),
            ),
        ),
    ),
    sent_strategy=SentCopyStrategy.IMAP_APPEND,
)
