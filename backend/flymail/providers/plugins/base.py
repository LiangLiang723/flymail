"""Data-driven provider plugin implementation shared by V2 providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from flymail.providers.contracts import (
    MailboxMapping,
    ProviderCapabilities,
    ProviderEndpoints,
    SentCopyStrategy,
)
from flymail.providers.errors import ProviderError, classify_provider_error


_SPECIAL_USE_MAPPING = {
    "\\inbox": "inbox",
    "\\sent": "sent",
    "\\drafts": "drafts",
    "\\junk": "junk",
    "\\spam": "junk",
    "\\trash": "trash",
    "\\archive": "archive",
    "\\all": "all_mail",
    "\\allmail": "all_mail",
    "\\important": "important",
}

_COMMON_ALIASES = {
    "inbox": "inbox",
    "收件箱": "inbox",
    "sent": "sent",
    "sent mail": "sent",
    "sent messages": "sent",
    "sent items": "sent",
    "outbox": "sent",
    "已发送": "sent",
    "已发送邮件": "sent",
    "发件箱": "sent",
    "draft": "drafts",
    "drafts": "drafts",
    "draft messages": "drafts",
    "草稿": "drafts",
    "草稿箱": "drafts",
    "junk": "junk",
    "junk email": "junk",
    "spam": "junk",
    "bulk": "junk",
    "bulk mail": "junk",
    "垃圾邮件": "junk",
    "垃圾箱": "junk",
    "trash": "trash",
    "deleted": "trash",
    "deleted items": "trash",
    "deleted messages": "trash",
    "bin": "trash",
    "已删除": "trash",
    "已删除邮件": "trash",
    "删除邮件": "trash",
    "archive": "archive",
    "archives": "archive",
    "归档": "archive",
    "all mail": "all_mail",
    "all messages": "all_mail",
    "所有邮件": "all_mail",
    "important": "important",
    "重要": "important",
}


@dataclass(frozen=True, slots=True)
class StaticProviderPlugin:
    key: str
    capability_set: ProviderCapabilities
    endpoint_set: ProviderEndpoints
    sent_strategy: SentCopyStrategy
    mailbox_aliases: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        normalized_key = str(self.key or "").strip().casefold()
        if not normalized_key or not normalized_key.replace("_", "").isalnum():
            raise ValueError("provider key is invalid")
        if not isinstance(self.capability_set, ProviderCapabilities):
            raise TypeError("capability_set must be ProviderCapabilities")
        if not isinstance(self.endpoint_set, ProviderEndpoints):
            raise TypeError("endpoint_set must be ProviderEndpoints")
        if not isinstance(self.sent_strategy, SentCopyStrategy):
            raise TypeError("sent_strategy must be SentCopyStrategy")
        aliases: list[tuple[str, str]] = []
        for raw_alias, raw_semantic in self.mailbox_aliases:
            alias = str(raw_alias or "").strip().casefold()
            semantic = str(raw_semantic or "").strip().casefold()
            if not alias:
                raise ValueError("mailbox alias is required")
            # MailboxMapping validates the semantic key without retaining this probe.
            MailboxMapping(native_key=alias, semantic_key=semantic)
            aliases.append((alias, semantic))
        if len({alias for alias, _semantic in aliases}) != len(aliases):
            raise ValueError("mailbox aliases must be unique")
        if (
            self.sent_strategy is SentCopyStrategy.PROVIDER_AUTO
            and not self.capability_set.auto_saves_sent_copy
        ):
            raise ValueError("provider-auto sent copy requires matching capability")
        if (
            self.sent_strategy is SentCopyStrategy.IMAP_APPEND
            and self.capability_set.auto_saves_sent_copy
        ):
            raise ValueError("auto-saved sent copy requires provider-auto strategy")
        object.__setattr__(self, "key", normalized_key)
        object.__setattr__(self, "mailbox_aliases", tuple(aliases))

    def capabilities(self) -> ProviderCapabilities:
        return self.capability_set

    def default_endpoints(self) -> ProviderEndpoints:
        return self.endpoint_set

    def map_mailbox(self, native_key: str, attributes: set[str]) -> MailboxMapping:
        preserved_native_key = str(native_key or "")
        if not preserved_native_key.strip():
            raise ValueError("native mailbox key is required")
        normalized_attributes = {
            str(attribute or "").strip().casefold()
            for attribute in attributes
            if str(attribute or "").strip()
        }
        semantic = next(
            (
                _SPECIAL_USE_MAPPING[attribute]
                for attribute in sorted(normalized_attributes)
                if attribute in _SPECIAL_USE_MAPPING
            ),
            None,
        )
        if semantic is None:
            lookup_key = preserved_native_key.strip().casefold()
            aliases = dict(_COMMON_ALIASES)
            aliases.update(self.mailbox_aliases)
            semantic = aliases.get(lookup_key, "custom")
        return MailboxMapping(
            native_key=preserved_native_key,
            semantic_key=semantic,
            attributes=tuple(normalized_attributes),
        )

    def classify_error(self, operation: str, response: object) -> ProviderError:
        return classify_provider_error(operation, response, provider_key=self.key)

    def sent_copy_strategy(self) -> SentCopyStrategy:
        return self.sent_strategy

    def normalize_labels(self, raw_labels: Sequence[str]) -> tuple[str, ...]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_label in raw_labels:
            label = str(raw_label or "").strip()
            if not label or label in seen:
                continue
            seen.add(label)
            normalized.append(label)
        return tuple(normalized)
