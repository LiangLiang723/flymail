"""Transactional batch ingestion of normalized remote message summaries."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Iterable

from flymail.domain.threading import (
    ThreadDecision,
    ThreadHeaders,
    ThreadResolver,
    canonical_message_key,
    normalize_message_id,
    normalize_subject,
    normalized_participants,
)
from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.db.uow import SqlUnitOfWork
from flymail.repositories.accounts import AccountRepository, MailAccount
from flymail.repositories.base import TenantContext
from flymail.repositories.mailboxes import Mailbox, MailboxRepository
from flymail.repositories.messages import (
    HeaderUpsert,
    MembershipUpsert,
    MessageRepository,
    MessageUpsert,
    RemoteInstanceUpsert,
)
from flymail.repositories.threads import (
    ThreadLink,
    ThreadRecord,
    ThreadRepository,
    ThreadSeed,
)
from flymail.repositories.users import UserRepository


@dataclass(frozen=True, slots=True)
class RemoteSummary:
    remote_uid: int
    uidvalidity: int
    message_id_header: str = ""
    in_reply_to: str = ""
    references: tuple[str, ...] = ()
    subject: str = ""
    from_addresses: tuple[str, ...] = ()
    to_addresses: tuple[str, ...] = ()
    cc_addresses: tuple[str, ...] = ()
    sent_at: float = 0
    received_at: float = 0
    size_bytes: int = 0
    flags: frozenset[str] = frozenset()
    has_attachments: bool = False
    snippet: str = ""
    provider_message_id: str = ""
    provider_thread_id: str = ""
    remote_version: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.remote_uid, bool) or int(self.remote_uid) < 1:
            raise ValueError("remote_uid must be positive")
        if isinstance(self.uidvalidity, bool) or int(self.uidvalidity) < 1:
            raise ValueError("uidvalidity must be positive")
        if isinstance(self.size_bytes, bool) or int(self.size_bytes) < 0:
            raise ValueError("size_bytes must be non-negative")
        sent_at = float(self.sent_at)
        received_at = float(self.received_at)
        if not math.isfinite(sent_at) or not math.isfinite(received_at):
            raise ValueError("message timestamps must be finite")
        if sent_at < 0 or received_at < 0:
            raise ValueError("message timestamps must be non-negative")
        if not isinstance(self.has_attachments, bool):
            raise TypeError("has_attachments must be bool")

        headers = ThreadHeaders(
            message_id_header=self.message_id_header,
            in_reply_to=self.in_reply_to,
            references=tuple(self.references),
        )
        object.__setattr__(self, "remote_uid", int(self.remote_uid))
        object.__setattr__(self, "uidvalidity", int(self.uidvalidity))
        object.__setattr__(self, "message_id_header", headers.message_id_header)
        object.__setattr__(self, "in_reply_to", headers.in_reply_to)
        object.__setattr__(self, "references", headers.references)
        object.__setattr__(self, "subject", str(self.subject or "").strip())
        object.__setattr__(
            self,
            "from_addresses",
            tuple(str(value).strip() for value in self.from_addresses if str(value).strip()),
        )
        object.__setattr__(
            self,
            "to_addresses",
            tuple(str(value).strip() for value in self.to_addresses if str(value).strip()),
        )
        object.__setattr__(
            self,
            "cc_addresses",
            tuple(str(value).strip() for value in self.cc_addresses if str(value).strip()),
        )
        object.__setattr__(self, "sent_at", sent_at)
        object.__setattr__(self, "received_at", received_at)
        object.__setattr__(self, "size_bytes", int(self.size_bytes))
        object.__setattr__(
            self,
            "flags",
            frozenset(str(flag).strip() for flag in self.flags if str(flag).strip()),
        )
        object.__setattr__(self, "snippet", str(self.snippet or "")[:4096])
        object.__setattr__(
            self,
            "provider_message_id",
            str(self.provider_message_id or "").strip(),
        )
        object.__setattr__(
            self,
            "provider_thread_id",
            str(self.provider_thread_id or "").strip(),
        )
        object.__setattr__(self, "remote_version", str(self.remote_version or "").strip()[:191])

    @property
    def is_read(self) -> bool:
        return "\\seen" in {flag.casefold() for flag in self.flags}

    @property
    def is_starred(self) -> bool:
        return "\\flagged" in {flag.casefold() for flag in self.flags}

    @property
    def participants(self) -> frozenset[str]:
        return normalized_participants(
            self.from_addresses,
            self.to_addresses,
            self.cc_addresses,
        )


@dataclass(frozen=True, slots=True)
class IngestionResult:
    messages_touched: int
    remote_instances_touched: int
    memberships_touched: int
    threads_touched: int
    projection_rows: int


@dataclass(frozen=True, slots=True)
class _PreparedSummary:
    summary: RemoteSummary
    canonical_message_key: str
    normalized_subject: str
    decision: ThreadDecision
    thread_record: ThreadRecord | None = None


class MessageIngestionService:
    def __init__(
        self,
        pool: DatabasePool,
        *,
        batch_limit: int = 500,
        fallback_window_seconds: int = 14 * 24 * 3600,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        if isinstance(batch_limit, bool) or int(batch_limit) < 1:
            raise ValueError("batch_limit must be positive")
        if isinstance(fallback_window_seconds, bool) or int(fallback_window_seconds) < 1:
            raise ValueError("fallback_window_seconds must be positive")
        self.pool = pool
        self.batch_limit = int(batch_limit)
        self.fallback_window_seconds = int(fallback_window_seconds)
        self.thread_resolver = ThreadResolver()

    async def ingest_batch(
        self,
        account: MailAccount,
        mailbox: Mailbox,
        summaries: Iterable[RemoteSummary],
    ) -> IngestionResult:
        if not isinstance(account, MailAccount):
            raise TypeError("account must be MailAccount")
        if not isinstance(mailbox, Mailbox):
            raise TypeError("mailbox must be Mailbox")
        if account.user_uid != mailbox.user_uid or account.id != mailbox.account_id:
            raise ValueError("account and mailbox scope do not match")
        batch = tuple(summaries)
        if len(batch) > self.batch_limit:
            raise ValueError("summary batch exceeds configured limit")
        if any(not isinstance(summary, RemoteSummary) for summary in batch):
            raise TypeError("summaries must contain RemoteSummary values")
        if not batch:
            return IngestionResult(0, 0, 0, 0, 0)

        tenant = TenantContext(account.user_uid)
        timestamp = time.time()
        async with SqlUnitOfWork(self.pool) as uow:
            if uow.connection is None:
                raise RuntimeError("ingestion unit of work has no connection")
            user_repository = UserRepository(uow.connection)
            account_repository = AccountRepository(uow.connection)
            mailbox_repository = MailboxRepository(uow.connection)
            message_repository = MessageRepository(uow.connection)
            thread_repository = ThreadRepository(uow.connection)

            if not await user_repository.lock_enabled_user_for_update(tenant):
                raise ValueError("user is not enabled")
            persisted_account = await account_repository.get_account(tenant, account.id)
            persisted_mailbox = await mailbox_repository.get_mailbox(tenant, mailbox.id)
            if persisted_account is None or persisted_mailbox is None:
                raise ValueError("account or mailbox does not belong to tenant")
            if persisted_mailbox.account_id != persisted_account.id:
                raise ValueError("mailbox does not belong to account")
            if persisted_account.status != "active":
                raise ValueError("mail account is not active")
            if persisted_mailbox.uidvalidity > 0 and any(
                summary.uidvalidity != persisted_mailbox.uidvalidity
                for summary in batch
            ):
                raise ValueError("summary UIDVALIDITY does not match mailbox")

            active_account = persisted_account
            active_mailbox = persisted_mailbox
            prepared: list[_PreparedSummary] = []
            thread_seeds: dict[str, ThreadSeed] = {}
            batch_fallbacks: list[tuple[str, frozenset[str], float, str]] = []
            for summary in batch:
                canonical_key = canonical_message_key(
                    user_uid=tenant.user_uid,
                    account_id=active_account.id,
                    mailbox_id=active_mailbox.id,
                    uidvalidity=summary.uidvalidity,
                    remote_uid=summary.remote_uid,
                    received_at=summary.received_at,
                    size_bytes=summary.size_bytes,
                    sender=summary.from_addresses[0] if summary.from_addresses else "",
                    message_id_header=summary.message_id_header,
                    provider_message_id=summary.provider_message_id,
                )
                normalized_subject_value = normalize_subject(summary.subject)
                header_decision = self.thread_resolver.resolve(
                    tenant.user_uid,
                    ThreadHeaders(
                        message_id_header=summary.message_id_header,
                        in_reply_to=summary.in_reply_to,
                        references=summary.references,
                    ),
                )
                fallback_record: ThreadRecord | None = None
                if header_decision is None:
                    fallback_record = await thread_repository.find_fallback_thread(
                        tenant,
                        normalized_subject=normalized_subject_value,
                        participants=summary.participants,
                        received_at=summary.received_at,
                        window_seconds=self.fallback_window_seconds,
                    )
                    if fallback_record is not None:
                        decision = ThreadDecision(
                            canonical_thread_key=fallback_record.canonical_thread_key,
                            parent_message_id_header="",
                            relation_source="fallback",
                            reason_code="subject_participants_time",
                        )
                    else:
                        batch_matches = [
                            candidate
                            for candidate in batch_fallbacks
                            if candidate[0] == normalized_subject_value
                            and bool(candidate[1] & summary.participants)
                            and abs(candidate[2] - summary.received_at)
                            <= self.fallback_window_seconds
                        ]
                        if batch_matches:
                            closest = min(
                                batch_matches,
                                key=lambda candidate: abs(candidate[2] - summary.received_at),
                            )
                            decision = ThreadDecision(
                                canonical_thread_key=closest[3],
                                parent_message_id_header="",
                                relation_source="fallback",
                                reason_code="batch_subject_participants_time",
                            )
                        else:
                            decision = ThreadDecision(
                                canonical_thread_key=self.thread_resolver.fallback_key(
                                    tenant.user_uid,
                                    canonical_key,
                                ),
                                parent_message_id_header="",
                                relation_source="fallback",
                                reason_code="new_fallback_thread",
                            )
                    batch_fallbacks.append(
                        (
                            normalized_subject_value,
                            summary.participants,
                            summary.received_at,
                            decision.canonical_thread_key,
                        )
                    )
                else:
                    decision = header_decision

                thread_seeds[decision.canonical_thread_key] = ThreadSeed(
                    canonical_thread_key=decision.canonical_thread_key,
                    normalized_subject=normalized_subject_value,
                    updated_at=timestamp,
                )
                prepared.append(
                    _PreparedSummary(
                        summary=summary,
                        canonical_message_key=canonical_key,
                        normalized_subject=normalized_subject_value,
                        decision=decision,
                        thread_record=fallback_record,
                    )
                )

            thread_records = await thread_repository.upsert_threads(
                tenant,
                thread_seeds.values(),
            )
            previous_thread_ids = await message_repository.message_threads_by_keys(
                tenant,
                (item.canonical_message_key for item in prepared),
            )
            messages = [
                MessageUpsert(
                    canonical_message_key=item.canonical_message_key,
                    message_id_header=item.summary.message_id_header,
                    thread_id=thread_records[item.decision.canonical_thread_key].id,
                    subject=item.summary.subject,
                    normalized_subject=item.normalized_subject,
                    from_addresses=item.summary.from_addresses,
                    to_addresses=item.summary.to_addresses,
                    cc_addresses=item.summary.cc_addresses,
                    sent_at=item.summary.sent_at,
                    received_at=item.summary.received_at,
                    size_bytes=item.summary.size_bytes,
                    has_attachments=item.summary.has_attachments,
                    snippet=item.summary.snippet,
                )
                for item in prepared
            ]
            message_ids = await message_repository.upsert_messages(
                tenant,
                messages,
                now=timestamp,
            )
            await message_repository.upsert_headers(
                tenant,
                (
                    HeaderUpsert(
                        canonical_message_key=item.canonical_message_key,
                        in_reply_to=item.summary.in_reply_to,
                        references=item.summary.references,
                        parsed_at=timestamp,
                    )
                    for item in prepared
                ),
                message_ids,
            )

            parent_headers = {
                item.decision.parent_message_id_header
                for item in prepared
                if item.decision.parent_message_id_header
            }
            parent_messages = await message_repository.message_ids_by_header(
                tenant,
                parent_headers,
            )
            links = []
            for item in prepared:
                parent_header = normalize_message_id(item.decision.parent_message_id_header)
                parent_message_id = (
                    parent_messages[parent_header][0]
                    if parent_header in parent_messages
                    else None
                )
                links.append(
                    ThreadLink(
                        thread_id=thread_records[item.decision.canonical_thread_key].id,
                        message_id=message_ids[item.canonical_message_key],
                        parent_message_id=parent_message_id,
                        relation_source=item.decision.relation_source,
                        position_hint=int(item.summary.received_at * 1000),
                        created_at=timestamp,
                    )
                )
            await thread_repository.link_messages(tenant, links)

            remote_records = [
                RemoteInstanceUpsert(
                    canonical_message_key=item.canonical_message_key,
                    account_id=active_account.id,
                    mailbox_id=active_mailbox.id,
                    uidvalidity=item.summary.uidvalidity,
                    remote_uid=item.summary.remote_uid,
                    provider_message_id=item.summary.provider_message_id,
                    provider_thread_id=item.summary.provider_thread_id,
                    flags=tuple(sorted(item.summary.flags)),
                    is_read=item.summary.is_read,
                    is_starred=item.summary.is_starred,
                    remote_version=item.summary.remote_version,
                    seen_at=timestamp,
                )
                for item in prepared
            ]
            remote_ids = await message_repository.upsert_remote_instances(
                tenant,
                remote_records,
                message_ids,
                now=timestamp,
            )
            membership_records = []
            for item in prepared:
                identity = (
                    active_account.id,
                    active_mailbox.id,
                    item.summary.uidvalidity,
                    item.summary.remote_uid,
                )
                membership_records.append(
                    MembershipUpsert(
                        remote_instance_id=remote_ids[identity],
                        mailbox_id=active_mailbox.id,
                        membership_kind=active_mailbox.mailbox_type,
                        provider_label=(
                            active_mailbox.native_key
                            if active_mailbox.mailbox_type == "label"
                            else ""
                        ),
                        updated_at=timestamp,
                    )
                )
            memberships_touched = await message_repository.upsert_memberships(
                tenant,
                membership_records,
            )
            await mailbox_repository.update_counts(
                tenant,
                active_mailbox.id,
                now=timestamp,
            )
            new_thread_ids = {
                thread_records[item.decision.canonical_thread_key].id
                for item in prepared
            }
            previous_ids = set(previous_thread_ids.values())
            affected_thread_ids = previous_ids | new_thread_ids
            projection_rows = await thread_repository.refresh_projections(
                tenant,
                affected_thread_ids,
                now=timestamp,
            )
            await thread_repository.remove_empty_threads(
                tenant,
                previous_ids - new_thread_ids,
            )
            await uow.commit()

        return IngestionResult(
            messages_touched=len({item.canonical_message_key for item in prepared}),
            remote_instances_touched=len(remote_ids),
            memberships_touched=memberships_touched,
            threads_touched=len(affected_thread_ids),
            projection_rows=projection_rows,
        )
