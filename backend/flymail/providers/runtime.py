"""Production provider runtime for durable Worker IMAP and SMTP operations."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import smtplib
import socket
import time
from datetime import datetime
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from collections.abc import AsyncIterable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

import aiomysql
from imapclient.imap_utf7 import decode as decode_imap_utf7

from flymail.config import FlyMailSettings
from flymail.domain.errors import PermanentError, RetryableError
from flymail.domain.ids import new_id
from flymail.domain.operations import (
    OperationKind,
    OperationRecord,
    RemoteApplyResult,
    RemoteOperationCommand,
    RemoteOperationState,
)
from flymail.infrastructure.db.pool import DatabasePool
from flymail.infrastructure.security.outbound import EndpointResolver, resolve_host
from flymail.providers.account_runtime import LoadedProviderAccount, ProviderAccountLoader
from flymail.providers.core.bodystructure import parse_bodystructure
from flymail.providers.core.mime_parts import select_message_parts
from flymail.providers.core.smtp_client import (
    SentAppendRequest,
    SentAppendResult,
    SentVerificationRequest,
    SentVerificationResult,
    SmtpDeliveryUncertain,
    SmtpSendRequest,
    SmtpSendResult,
)
from flymail.providers.errors import ProviderError
from flymail.providers.network import (
    BlockingImapSession,
    BlockingSmtpSession,
    ResolvedAccountEndpoints,
    decode_runtime_credential,
    resolve_account_endpoints,
)
from flymail.providers.registry import ProviderRegistry
from flymail.repositories.base import TenantContext, fetch_all, fetch_one
from flymail.repositories.mailboxes import Mailbox, MailboxRepository
from flymail.workers.content_fetch import ContentFetchService
from flymail.workers.ingestion import MessageIngestionService, RemoteSummary
from flymail.workers.dispatcher import JobContext, JobOutcome


@dataclass(frozen=True, slots=True)
class RuntimeRemoteLocator:
    remote_instance_id: str
    account_id: str
    user_uid: str
    mailbox_native_key: str
    remote_uid: int
    mailbox_native_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "remote_instance_id",
            "account_id",
            "user_uid",
            "mailbox_native_key",
        ):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"{field_name} is required")
            object.__setattr__(self, field_name, value)
        uid = int(self.remote_uid)
        if uid < 1:
            raise ValueError("remote_uid must be positive")
        object.__setattr__(self, "remote_uid", uid)
        object.__setattr__(
            self,
            "mailbox_native_keys",
            tuple(
                dict.fromkeys(
                    str(value or "").strip()
                    for value in self.mailbox_native_keys
                    if str(value or "").strip()
                )
            ),
        )


class ProviderStateStore(Protocol):
    async def remote_locator(
        self,
        remote_instance_id: str,
        *,
        expected_user_uid: str | None = None,
    ) -> RuntimeRemoteLocator | None: ...

    async def sent_mailbox(
        self,
        account_id: str,
        *,
        expected_user_uid: str | None = None,
    ) -> str: ...


class DatabaseProviderStateStore:
    def __init__(self, pool: DatabasePool) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        self.pool = pool

    async def remote_locator(
        self,
        remote_instance_id: str,
        *,
        expected_user_uid: str | None = None,
    ) -> RuntimeRemoteLocator | None:
        async with self.pool.acquire() as connection:
            row = await fetch_one(
                connection,
                """
                SELECT ri.id, ri.account_id, ri.user_uid, ri.remote_uid,
                       mailbox.native_key AS mailbox_native_key
                FROM message_remote_instances ri
                JOIN mailboxes mailbox
                  ON mailbox.id = ri.mailbox_id
                 AND mailbox.user_uid = ri.user_uid
                WHERE ri.id = %s AND ri.remote_deleted = 0
                """,
                (str(remote_instance_id or "").strip(),),
            )
            if row is None:
                return None
            if expected_user_uid is not None and str(row["user_uid"]) != str(expected_user_uid):
                raise ValueError("remote message does not belong to expected user")
            memberships = await fetch_all(
                connection,
                """
                SELECT mailbox.native_key
                FROM message_memberships membership
                JOIN mailboxes mailbox
                  ON mailbox.id = membership.mailbox_id
                 AND mailbox.user_uid = membership.user_uid
                WHERE membership.remote_instance_id = %s
                  AND membership.user_uid = %s
                ORDER BY mailbox.mailbox_type, mailbox.native_key, mailbox.id
                """,
                (str(row["id"]), str(row["user_uid"])),
            )
        return RuntimeRemoteLocator(
            remote_instance_id=str(row["id"]),
            account_id=str(row["account_id"]),
            user_uid=str(row["user_uid"]),
            mailbox_native_key=str(row["mailbox_native_key"]),
            remote_uid=int(row["remote_uid"]),
            mailbox_native_keys=tuple(str(item["native_key"]) for item in memberships),
        )

    async def sent_mailbox(
        self,
        account_id: str,
        *,
        expected_user_uid: str | None = None,
    ) -> str:
        conditions = ["account_id = %s", "semantic_key = 'sent'"]
        params: list[object] = [str(account_id or "").strip()]
        if expected_user_uid is not None:
            conditions.append("user_uid = %s")
            params.append(str(expected_user_uid))
        async with self.pool.acquire() as connection:
            row = await fetch_one(
                connection,
                f"""
                SELECT native_key
                FROM mailboxes
                WHERE {' AND '.join(conditions)}
                ORDER BY mailbox_type, id
                LIMIT 1
                """,
                tuple(params),
            )
        if row is None:
            raise ValueError("sent mailbox is unavailable")
        return str(row["native_key"])


ImapSessionFactory = Callable[[LoadedProviderAccount], Any]
SmtpSessionFactory = Callable[[LoadedProviderAccount], Any]


class ProductionProviderRuntime:
    """Load tenant credentials and execute bounded provider operations."""

    def __init__(
        self,
        pool: DatabasePool,
        settings: FlyMailSettings,
        *,
        registry: ProviderRegistry | None = None,
        account_loader: ProviderAccountLoader | None = None,
        state_store: ProviderStateStore | None = None,
        endpoint_resolver: EndpointResolver = resolve_host,
        imap_session_factory: ImapSessionFactory | None = None,
        smtp_session_factory: SmtpSessionFactory | None = None,
    ) -> None:
        if not isinstance(pool, DatabasePool):
            raise TypeError("pool must be DatabasePool")
        if not isinstance(settings, FlyMailSettings) or settings.role != "worker":
            raise TypeError("settings must be FlyMailSettings for worker role")
        self.pool = pool
        self.settings = settings
        self.registry = registry or ProviderRegistry.default()
        self.account_loader = account_loader or ProviderAccountLoader(
            pool,
            settings.session_secret,
            registry=self.registry,
            endpoint_resolver=endpoint_resolver,
        )
        self.state_store = state_store or DatabaseProviderStateStore(pool)
        self.imap_session_factory = imap_session_factory or self._imap_session
        self.smtp_session_factory = smtp_session_factory or self._smtp_session
        self.ingestion = MessageIngestionService(pool)
        self.content_service: ContentFetchService | None = None

    def bind_content_service(self, service: ContentFetchService) -> None:
        if not isinstance(service, ContentFetchService):
            raise TypeError("service must be ContentFetchService")
        self.content_service = service

    @staticmethod
    def _imap_session(loaded: LoadedProviderAccount) -> BlockingImapSession:
        return BlockingImapSession(
            loaded.endpoints.imap,
            loaded.credential,
            proxy_url=loaded.proxy_url,
        )

    @staticmethod
    def _smtp_session(loaded: LoadedProviderAccount) -> BlockingSmtpSession:
        return BlockingSmtpSession(
            loaded.endpoints.smtp,
            loaded.credential,
            proxy_url=loaded.proxy_url,
        )

    async def verify(
        self,
        *,
        account,
        credential_type: str,
        credential: bytes,
        endpoint_config: Mapping[str, Mapping[str, object]],
        proxy_url: str | None,
    ) -> None:
        configured_account = replace(account, endpoint_config=dict(endpoint_config))
        loaded = LoadedProviderAccount(
            account=configured_account,
            endpoints=resolve_account_endpoints(configured_account, self.registry),
            credential=decode_runtime_credential(
                configured_account,
                credential_type,
                credential,
            ),
            proxy_url=proxy_url,
        )
        try:
            await asyncio.to_thread(self._verify_blocking, loaded)
        except ProviderError:
            raise
        except Exception as exc:
            raise self.registry.get(account.provider_key).classify_error(
                "account.verify",
                exc,
            ) from exc

    def _verify_blocking(self, loaded: LoadedProviderAccount) -> None:
        with self.imap_session_factory(loaded) as imap:
            imap.client.list_folders()
        with self.smtp_session_factory(loaded):
            return None

    async def cleanup(self, **_kwargs) -> None:
        return None

    def stream(self, locator, fetch_spec: str) -> AsyncIterable[bytes]:
        async def chunks():
            loaded = await self.account_loader.load(locator.account_id)
            try:
                value = await asyncio.to_thread(
                    self._fetch_part_blocking,
                    loaded,
                    locator.mailbox_native_key,
                    int(locator.remote_uid),
                    str(fetch_spec or ""),
                )
            except ProviderError:
                raise
            except Exception as exc:
                raise self.registry.get(loaded.account.provider_key).classify_error(
                    "imap.fetch",
                    exc,
                ) from exc
            yield value

        return chunks()

    def _fetch_part_blocking(
        self,
        loaded: LoadedProviderAccount,
        mailbox_native_key: str,
        remote_uid: int,
        fetch_spec: str,
    ) -> bytes:
        with self.imap_session_factory(loaded) as session:
            session.client.select_folder(mailbox_native_key, readonly=True)
            result = session.client.fetch([remote_uid], [fetch_spec])
        row = self._fetch_row(result, remote_uid)
        for key, value in row.items():
            normalized = self._key_text(key).upper()
            if "BODY[" in normalized or "RFC822" in normalized:
                if isinstance(value, bytes):
                    return value
                if isinstance(value, bytearray | memoryview):
                    return bytes(value)
        raise ValueError("IMAP response did not contain requested message bytes")

    async def observe(self, operation: OperationRecord) -> RemoteOperationState | None:
        locator = await self.state_store.remote_locator(
            operation.remote_instance_id,
            expected_user_uid=operation.user_uid,
        )
        if locator is None:
            return None
        loaded = await self.account_loader.load(
            locator.account_id,
            expected_user_uid=locator.user_uid,
        )
        try:
            return await asyncio.to_thread(self._observe_blocking, loaded, locator)
        except Exception as exc:
            raise self.registry.get(loaded.account.provider_key).classify_error(
                "imap.observe",
                exc,
            ) from exc

    def _observe_blocking(
        self,
        loaded: LoadedProviderAccount,
        locator: RuntimeRemoteLocator,
    ) -> RemoteOperationState | None:
        with self.imap_session_factory(loaded) as session:
            session.client.select_folder(locator.mailbox_native_key, readonly=True)
            result = session.client.fetch(
                [locator.remote_uid],
                ["FLAGS", "MODSEQ", "X-GM-LABELS"],
            )
        if not result:
            return None
        row = self._fetch_row(result, locator.remote_uid)
        flags = self._text_values(row.get(b"FLAGS", row.get("FLAGS", ())))
        labels = self._text_values(
            row.get(b"X-GM-LABELS", row.get("X-GM-LABELS", ()))
        )
        mailbox_keys = tuple(dict.fromkeys((*locator.mailbox_native_keys, *labels)))
        return RemoteOperationState(
            remote_version=self._remote_version(row, flags, mailbox_keys),
            is_read="\\Seen" in flags,
            is_starred="\\Flagged" in flags,
            mailbox_native_keys=mailbox_keys,
        )

    async def apply(self, command: RemoteOperationCommand) -> RemoteApplyResult:
        locator = await self.state_store.remote_locator(command.remote_instance_id)
        if locator is None:
            raise PermanentError("remote message no longer exists")
        if locator.account_id != command.account_id:
            raise PermanentError("remote operation account scope is invalid")
        loaded = await self.account_loader.load(
            locator.account_id,
            expected_user_uid=locator.user_uid,
        )
        try:
            return await asyncio.to_thread(
                self._apply_blocking,
                loaded,
                locator,
                command,
            )
        except (PermanentError, RetryableError):
            raise
        except Exception as exc:
            error = self.registry.get(loaded.account.provider_key).classify_error(
                "imap.operation",
                exc,
            )
            if error.retryable:
                raise RetryableError(error.safe_detail) from exc
            raise PermanentError(error.safe_detail) from exc

    def _apply_blocking(
        self,
        loaded: LoadedProviderAccount,
        locator: RuntimeRemoteLocator,
        command: RemoteOperationCommand,
    ) -> RemoteApplyResult:
        with self.imap_session_factory(loaded) as session:
            client = session.client
            client.select_folder(locator.mailbox_native_key, readonly=False)
            messages = [locator.remote_uid]
            if command.kind is OperationKind.SET_READ:
                method = client.add_flags if command.desired_value else client.remove_flags
                method(messages, ["\\Seen"], silent=True)
            elif command.kind is OperationKind.SET_STARRED:
                method = client.add_flags if command.desired_value else client.remove_flags
                method(messages, ["\\Flagged"], silent=True)
            elif command.remote_action == "add_label":
                client.add_gmail_labels(messages, [command.target_native_key], silent=True)
            elif command.remote_action == "remove_label":
                client.remove_gmail_labels(messages, [command.target_native_key], silent=True)
            elif command.remote_action == "move":
                try:
                    client.move(messages, command.target_native_key)
                except Exception:
                    if not command.allow_copy_delete:
                        raise
                    client.copy(messages, command.target_native_key)
                    client.delete_messages(messages, silent=True)
                    client.expunge(messages)
                return RemoteApplyResult(
                    self._terminal_remote_version(command)
                )
            elif command.remote_action == "delete_permanent":
                client.delete_messages(messages, silent=True)
                client.expunge(messages)
                return RemoteApplyResult(
                    self._terminal_remote_version(command)
                )
            else:
                raise PermanentError("remote operation is unsupported")
            result = client.fetch(messages, ["FLAGS", "MODSEQ", "X-GM-LABELS"])
        row = self._fetch_row(result, locator.remote_uid)
        flags = self._text_values(row.get(b"FLAGS", row.get("FLAGS", ())))
        labels = self._text_values(
            row.get(b"X-GM-LABELS", row.get("X-GM-LABELS", ()))
        )
        return RemoteApplyResult(
            self._remote_version(row, flags, tuple(dict.fromkeys(labels)))
        )

    async def send(self, request: SmtpSendRequest) -> SmtpSendResult:
        loaded = await self.account_loader.load(request.account_id)
        try:
            return await asyncio.to_thread(self._send_blocking, loaded, request)
        except SmtpDeliveryUncertain:
            raise
        except smtplib.SMTPRecipientsRefused as exc:
            raise PermanentError("SMTP rejected all recipients") from exc
        except smtplib.SMTPAuthenticationError as exc:
            raise PermanentError("SMTP authentication failed") from exc
        except smtplib.SMTPDataError as exc:
            code = int(getattr(exc, "smtp_code", 0) or 0)
            if 500 <= code <= 599:
                raise PermanentError("SMTP permanently rejected the message") from exc
            raise RetryableError("SMTP temporarily rejected the message") from exc
        except (socket.timeout, TimeoutError, OSError, smtplib.SMTPServerDisconnected) as exc:
            raise RetryableError("SMTP connection failed") from exc

    def _send_blocking(
        self,
        loaded: LoadedProviderAccount,
        request: SmtpSendRequest,
    ) -> SmtpSendResult:
        with self.smtp_session_factory(loaded) as session:
            try:
                refused = session.client.sendmail(
                    request.envelope_from,
                    list(request.envelope_recipients),
                    request.source,
                    mail_options=("SMTPUTF8",) if request.use_smtp_utf8 else (),
                )
            except smtplib.SMTPServerDisconnected as exc:
                raise SmtpDeliveryUncertain(
                    "SMTP connection ended before acceptance was confirmed"
                ) from exc
        if refused:
            if len(refused) < len(request.envelope_recipients):
                raise SmtpDeliveryUncertain(
                    "SMTP accepted at least one recipient but rejected others"
                )
            raise smtplib.SMTPRecipientsRefused(refused)
        return SmtpSendResult(250, "accepted by SMTP server")

    async def verify_sent(
        self,
        request: SentVerificationRequest,
    ) -> SentVerificationResult:
        loaded = await self.account_loader.load(request.account_id)
        sent_mailbox = await self.state_store.sent_mailbox(request.account_id)
        try:
            values = await asyncio.to_thread(
                self._search_sent_blocking,
                loaded,
                sent_mailbox,
                request.message_id_header,
            )
        except Exception as exc:
            raise RetryableError("sent-message verification failed") from exc
        if not values:
            return SentVerificationResult(False)
        return SentVerificationResult(True, remote_uid=int(values[0]))

    def _search_sent_blocking(
        self,
        loaded: LoadedProviderAccount,
        sent_mailbox: str,
        message_id_header: str,
    ) -> list[int]:
        with self.imap_session_factory(loaded) as session:
            session.client.select_folder(sent_mailbox, readonly=True)
            values = session.client.search(
                ["HEADER", "Message-ID", message_id_header]
            )
        return [int(value) for value in values]

    async def append_sent_copy(self, request: SentAppendRequest) -> SentAppendResult:
        loaded = await self.account_loader.load(request.account_id)
        sent_mailbox = await self.state_store.sent_mailbox(request.account_id)
        try:
            result = await asyncio.to_thread(
                self._append_sent_blocking,
                loaded,
                sent_mailbox,
                request.source,
            )
        except Exception as exc:
            raise RetryableError("sent-copy append failed") from exc
        return SentAppendResult(remote_uid=self._append_uid(result))

    def _append_sent_blocking(
        self,
        loaded: LoadedProviderAccount,
        sent_mailbox: str,
        source: bytes,
    ):
        with self.imap_session_factory(loaded) as session:
            return session.client.append(
                sent_mailbox,
                source,
                flags=("\\Seen",),
            )

    async def synchronize(
        self,
        context: JobContext,
        payload: Mapping[str, object],
        *,
        job_kind: str,
    ) -> JobOutcome:
        account_id = str(payload.get("account_id") or context.account_id or "").strip()
        if (
            not context.user_uid
            or not context.account_id
            or not context.provider_key
            or account_id != context.account_id
        ):
            return JobOutcome.fail("InvalidSyncScope", "sync job scope is invalid")
        if context.stop_event.is_set():
            return JobOutcome.retry(
                "WorkerStopping",
                "mail synchronization paused for shutdown",
                base_seconds=1,
                max_seconds=30,
            )
        try:
            loaded = await self.account_loader.load(
                account_id,
                expected_user_uid=context.user_uid,
            )
            if loaded.account.provider_key != context.provider_key:
                return JobOutcome.fail(
                    "InvalidSyncScope",
                    "sync provider scope is invalid",
                )
            if job_kind == "sync.mailbox_refresh":
                await self._refresh_mailboxes(loaded)
                return JobOutcome.success()
            mailbox_id = str(payload.get("mailbox_id") or "").strip()
            mailboxes = await self._mailboxes_for_sync(
                loaded.account.user_uid,
                loaded.account.id,
                mailbox_id=mailbox_id or None,
            )
            if not mailboxes:
                if mailbox_id:
                    return JobOutcome.fail(
                        "MailboxNotFound",
                        "sync mailbox was not found",
                    )
                await self._refresh_mailboxes(loaded)
                mailboxes = await self._mailboxes_for_sync(
                    loaded.account.user_uid,
                    loaded.account.id,
                    mailbox_id=None,
                )
            has_more = False
            for mailbox in mailboxes:
                if context.stop_event.is_set():
                    return JobOutcome.retry(
                        "WorkerStopping",
                        "mail synchronization paused for shutdown",
                        base_seconds=1,
                        max_seconds=30,
                    )
                has_more = (
                    await self._sync_mailbox(loaded, mailbox, job_kind=job_kind)
                    or has_more
                )
            if has_more:
                return JobOutcome.retry(
                    "SyncContinuation",
                    "mail synchronization has more summaries",
                    base_seconds=0,
                    max_seconds=0,
                )
            return JobOutcome.success()
        except ProviderError as exc:
            if exc.retryable:
                return JobOutcome.retry(exc.code.value, exc.safe_detail)
            return JobOutcome.fail(exc.code.value, exc.safe_detail)
        except RetryableError:
            return JobOutcome.retry(
                "ProviderSyncRetryable",
                "mail synchronization will be retried",
            )
        except (PermanentError, ValueError):
            return JobOutcome.fail(
                "ProviderSyncPermanent",
                "mail synchronization cannot continue",
            )
        except Exception:
            return JobOutcome.retry(
                "ProviderSyncUnexpected",
                "mail synchronization failed unexpectedly",
            )

    async def _refresh_mailboxes(self, loaded: LoadedProviderAccount) -> None:
        try:
            remote_mailboxes = await asyncio.to_thread(
                self._refresh_mailboxes_blocking,
                loaded,
            )
        except Exception as exc:
            raise self.registry.get(loaded.account.provider_key).classify_error(
                "imap.mailbox_refresh",
                exc,
            ) from exc
        tenant = TenantContext(loaded.account.user_uid)
        timestamp = time.time()
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                repository = MailboxRepository(connection)
                for item in remote_mailboxes:
                    mailbox = await repository.upsert_mailbox(
                        tenant,
                        account_id=loaded.account.id,
                        native_key=str(item["native_key"]),
                        native_name=str(item["native_name"]),
                        semantic_key=str(item["semantic_key"]),
                        mailbox_type=str(item["mailbox_type"]),
                        delimiter_value=str(item["delimiter"]),
                        attributes=tuple(item["attributes"]),
                        uidvalidity=int(item["uidvalidity"]),
                        highest_modseq=int(item["highest_modseq"]),
                        now=timestamp,
                    )
                    async with connection.cursor() as cursor:
                        await cursor.execute(
                            """
                            UPDATE mailboxes
                            SET total_count=%s, unread_count=%s,
                                sync_status='ready', updated_at=%s
                            WHERE id=%s AND user_uid=%s
                            """,
                            (
                                int(item["total_count"]),
                                int(item["unread_count"]),
                                timestamp,
                                mailbox.id,
                                tenant.user_uid,
                            ),
                        )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

    def _refresh_mailboxes_blocking(
        self,
        loaded: LoadedProviderAccount,
    ) -> tuple[dict[str, object], ...]:
        plugin = self.registry.get(loaded.account.provider_key)
        values: list[dict[str, object]] = []
        with self.imap_session_factory(loaded) as session:
            for raw_attributes, raw_delimiter, raw_native_key in session.client.list_folders():
                native_key = self._key_text(raw_native_key)
                delimiter = self._key_text(raw_delimiter)
                attributes = self._text_values(raw_attributes)
                mapping = plugin.map_mailbox(native_key, set(attributes))
                status: Mapping = {}
                try:
                    status = session.client.folder_status(
                        native_key,
                        ["UIDVALIDITY", "HIGHESTMODSEQ", "MESSAGES", "UNSEEN"],
                    )
                except Exception:
                    status = {}
                uidvalidity = self._mapping_int(status, "UIDVALIDITY")
                highest_modseq = self._mapping_int(status, "HIGHESTMODSEQ")
                values.append(
                    {
                        "native_key": native_key,
                        "native_name": native_key,
                        "semantic_key": mapping.semantic_key,
                        "mailbox_type": (
                            "label"
                            if loaded.account.provider_key == "gmail"
                            and mapping.semantic_key == "custom"
                            else "folder"
                        ),
                        "delimiter": delimiter,
                        "attributes": mapping.attributes,
                        "uidvalidity": uidvalidity,
                        "highest_modseq": highest_modseq,
                        "total_count": self._mapping_int(status, "MESSAGES"),
                        "unread_count": self._mapping_int(status, "UNSEEN"),
                    }
                )
        return tuple(values)

    async def _mailboxes_for_sync(
        self,
        user_uid: str,
        account_id: str,
        *,
        mailbox_id: str | None,
    ) -> tuple[Mailbox, ...]:
        tenant = TenantContext(user_uid)
        async with self.pool.acquire() as connection:
            repository = MailboxRepository(connection)
            if mailbox_id:
                mailbox = await repository.get_mailbox(tenant, mailbox_id)
                if mailbox is None or mailbox.account_id != account_id:
                    return ()
                return (mailbox,)
            async with connection.cursor() as cursor:
                await cursor.execute(
                    """
                    SELECT id
                    FROM mailboxes
                    WHERE user_uid=%s AND account_id=%s
                      AND mailbox_type='folder'
                    ORDER BY CASE semantic_key
                        WHEN 'inbox' THEN 0
                        WHEN 'sent' THEN 1
                        ELSE 2 END,
                        native_key, id
                    """,
                    (user_uid, account_id),
                )
                rows = await cursor.fetchall()
            values: list[Mailbox] = []
            for row in rows:
                mailbox = await repository.get_mailbox(tenant, str(row[0]))
                if mailbox is not None:
                    values.append(mailbox)
            return tuple(values)

    async def _sync_mailbox(
        self,
        loaded: LoadedProviderAccount,
        mailbox: Mailbox,
        *,
        job_kind: str,
    ) -> bool:
        last_uid, _cursor_modseq = await self._sync_cursor(
            loaded.account.user_uid,
            loaded.account.id,
            mailbox.id,
        )
        batch_limit = self.registry.get(
            loaded.account.provider_key
        ).capabilities().max_fetch_batch
        try:
            batch = await asyncio.to_thread(
                self._fetch_summary_batch_blocking,
                loaded,
                mailbox,
                last_uid,
                batch_limit,
                job_kind,
            )
        except Exception as exc:
            raise self.registry.get(loaded.account.provider_key).classify_error(
                "imap.summary_sync",
                exc,
            ) from exc
        summaries = tuple(item[0] for item in batch["items"])
        if summaries:
            await self.ingestion.ingest_batch(
                loaded.account,
                replace(
                    mailbox,
                    uidvalidity=int(batch["uidvalidity"]),
                    highest_modseq=int(batch["highest_modseq"]),
                ),
                summaries,
            )
            await self._record_sync_structures(
                loaded.account.user_uid,
                loaded.account.id,
                mailbox.id,
                tuple((summary.remote_uid, tree) for summary, tree in batch["items"]),
            )
            last_uid = max(summary.remote_uid for summary in summaries)
        await self._store_sync_cursor(
            loaded.account.user_uid,
            loaded.account.id,
            mailbox.id,
            last_uid=last_uid,
            highest_modseq=int(batch["highest_modseq"]),
        )
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                await MailboxRepository(connection).update_counts(
                    TenantContext(loaded.account.user_uid),
                    mailbox.id,
                )
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        UPDATE mailboxes
                        SET uidvalidity=%s, highest_modseq=%s,
                            sync_status='ready', updated_at=%s
                        WHERE id=%s AND user_uid=%s
                        """,
                        (
                            int(batch["uidvalidity"]),
                            int(batch["highest_modseq"]),
                            time.time(),
                            mailbox.id,
                            loaded.account.user_uid,
                        ),
                    )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise
        return bool(batch["has_more"])

    def _fetch_summary_batch_blocking(
        self,
        loaded: LoadedProviderAccount,
        mailbox: Mailbox,
        last_uid: int,
        batch_limit: int,
        job_kind: str,
    ) -> dict[str, object]:
        with self.imap_session_factory(loaded) as session:
            selected = session.client.select_folder(mailbox.native_key, readonly=True)
            uidvalidity = self._mapping_int(selected, "UIDVALIDITY") or mailbox.uidvalidity or 1
            highest_modseq = self._mapping_int(selected, "HIGHESTMODSEQ")
            limit = max(int(batch_limit), 1)
            if job_kind == "sync.reconcile":
                all_uids = sorted(
                    {
                        int(value)
                        for value in session.client.search(["ALL"])
                        if int(value) > 0
                    }
                )
                new_uids = [uid for uid in all_uids if uid > int(last_uid)]
                selected_new = new_uids[:limit]
                selected_set = set(selected_new)
                remaining = limit - len(selected_new)
                selected_recent = (
                    [
                        uid
                        for uid in reversed(all_uids)
                        if uid not in selected_set
                    ][:remaining]
                    if remaining > 0
                    else []
                )
                selected_uids = sorted((*selected_new, *selected_recent))
                has_more = len(new_uids) > len(selected_new)
            else:
                remote_uids = sorted(
                    {
                        int(value)
                        for value in session.client.search(
                            ["UID", f"{max(int(last_uid), 0) + 1}:*"]
                        )
                        if int(value) > int(last_uid)
                    }
                )
                selected_uids = remote_uids[:limit]
                has_more = len(remote_uids) > len(selected_uids)
            if not selected_uids:
                return {
                    "uidvalidity": uidvalidity,
                    "highest_modseq": highest_modseq,
                    "items": (),
                    "has_more": False,
                }
            data = session.client.fetch(
                selected_uids,
                [
                    "FLAGS",
                    "INTERNALDATE",
                    "RFC822.SIZE",
                    "BODY.PEEK[HEADER.FIELDS (MESSAGE-ID IN-REPLY-TO REFERENCES SUBJECT FROM TO CC DATE)]",
                    "BODYSTRUCTURE",
                    "MODSEQ",
                    "X-GM-MSGID",
                    "X-GM-THRID",
                ],
            )
        items = tuple(
            self._summary_from_fetch(
                uid,
                uidvalidity,
                self._fetch_row(data, uid),
            )
            for uid in selected_uids
        )
        return {
            "uidvalidity": uidvalidity,
            "highest_modseq": highest_modseq,
            "items": items,
            "has_more": has_more,
        }

    def _summary_from_fetch(
        self,
        remote_uid: int,
        uidvalidity: int,
        row: Mapping,
    ) -> tuple[RemoteSummary, object | None]:
        header_bytes = b""
        for key, value in row.items():
            normalized = self._key_text(key).upper()
            if "HEADER.FIELDS" in normalized and isinstance(value, bytes):
                header_bytes = value
                break
        message = BytesParser(policy=policy.default).parsebytes(header_bytes)
        flags = frozenset(
            self._text_values(row.get(b"FLAGS", row.get("FLAGS", ())))
        )
        internal_date = row.get(b"INTERNALDATE", row.get("INTERNALDATE"))
        received_at = self._timestamp(internal_date)
        sent_at = self._timestamp(message.get("Date")) or received_at
        provider_labels = self._text_values(
            row.get(b"X-GM-LABELS", row.get("X-GM-LABELS", ()))
        )
        bodystructure = row.get(b"BODYSTRUCTURE", row.get("BODYSTRUCTURE"))
        tree = None
        has_attachments = False
        if bodystructure:
            try:
                tree = parse_bodystructure(bodystructure)
                selection = select_message_parts(tree)
                has_attachments = bool(selection.attachment_parts)
            except (TypeError, ValueError):
                tree = None
        summary = RemoteSummary(
            remote_uid=remote_uid,
            uidvalidity=uidvalidity,
            message_id_header=str(message.get("Message-ID") or ""),
            in_reply_to=str(message.get("In-Reply-To") or ""),
            references=tuple(str(message.get("References") or "").split()),
            subject=str(message.get("Subject") or ""),
            from_addresses=self._addresses(message.get_all("From", ())),
            to_addresses=self._addresses(message.get_all("To", ())),
            cc_addresses=self._addresses(message.get_all("Cc", ())),
            sent_at=sent_at,
            received_at=received_at,
            size_bytes=self._mapping_int(row, "RFC822.SIZE"),
            flags=flags,
            has_attachments=has_attachments,
            snippet="",
            provider_message_id=str(
                row.get(b"X-GM-MSGID", row.get("X-GM-MSGID", "")) or ""
            ),
            provider_thread_id=str(
                row.get(b"X-GM-THRID", row.get("X-GM-THRID", "")) or ""
            ),
            remote_version=self._remote_version(row, tuple(flags), provider_labels),
            provider_labels=provider_labels,
        )
        return summary, tree

    async def _record_sync_structures(
        self,
        user_uid: str,
        account_id: str,
        mailbox_id: str,
        structures: tuple[tuple[int, object | None], ...],
    ) -> None:
        if self.content_service is None:
            raise ValueError("content service is not bound to provider runtime")
        valid_uids = tuple(uid for uid, tree in structures if tree is not None)
        if not valid_uids:
            return
        placeholders = ",".join("%s" for _ in valid_uids)
        async with self.pool.acquire() as connection:
            rows = await fetch_all(
                connection,
                f"""
                SELECT remote_uid, id AS remote_instance_id, message_id
                FROM message_remote_instances
                WHERE user_uid=%s AND account_id=%s AND mailbox_id=%s
                  AND remote_uid IN ({placeholders})
                  AND remote_deleted=0
                """,
                (user_uid, account_id, mailbox_id, *valid_uids),
            )
        persisted = {int(row["remote_uid"]): row for row in rows}
        for remote_uid, tree in structures:
            if tree is None or remote_uid not in persisted:
                continue
            row = persisted[remote_uid]
            await self.content_service.record_structure(
                TenantContext(user_uid),
                message_id=str(row["message_id"]),
                remote_instance_id=str(row["remote_instance_id"]),
                tree=tree,
            )

    async def _sync_cursor(
        self,
        user_uid: str,
        account_id: str,
        mailbox_id: str,
    ) -> tuple[int, int]:
        async with self.pool.acquire() as connection:
            row = await fetch_one(
                connection,
                """
                SELECT last_uid, highest_modseq
                FROM sync_cursors
                WHERE user_uid=%s AND account_id=%s AND mailbox_id=%s
                  AND phase='summary'
                """,
                (user_uid, account_id, mailbox_id),
            )
        if row is None:
            return 0, 0
        return int(row["last_uid"] or 0), int(row["highest_modseq"] or 0)

    async def _store_sync_cursor(
        self,
        user_uid: str,
        account_id: str,
        mailbox_id: str,
        *,
        last_uid: int,
        highest_modseq: int,
    ) -> None:
        async with self.pool.acquire() as connection:
            await connection.begin()
            try:
                async with connection.cursor() as cursor:
                    await cursor.execute(
                        """
                        INSERT INTO sync_cursors (
                            id, user_uid, account_id, mailbox_id, phase,
                            cursor_type, cursor_json, last_uid,
                            highest_modseq, updated_at
                        ) VALUES (%s, %s, %s, %s, 'summary', 'uid', NULL,
                                  %s, %s, %s) AS incoming
                        ON DUPLICATE KEY UPDATE
                            user_uid=incoming.user_uid,
                            last_uid=GREATEST(sync_cursors.last_uid, incoming.last_uid),
                            highest_modseq=GREATEST(
                                sync_cursors.highest_modseq,
                                incoming.highest_modseq
                            ),
                            updated_at=incoming.updated_at
                        """,
                        (
                            new_id("cur"),
                            user_uid,
                            account_id,
                            mailbox_id,
                            max(int(last_uid), 0),
                            max(int(highest_modseq), 0),
                            time.time(),
                        ),
                    )
                await connection.commit()
            except Exception:
                await connection.rollback()
                raise

    @classmethod
    def _mapping_int(cls, value: Mapping, key: str) -> int:
        raw = value.get(key)
        if raw is None:
            raw = value.get(key.encode("ascii"))
        if isinstance(raw, (tuple, list)) and raw:
            raw = raw[0]
        try:
            return max(int(raw or 0), 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _timestamp(value: object) -> float:
        if isinstance(value, datetime):
            return max(value.timestamp(), 0.0)
        if isinstance(value, str) and value.strip():
            try:
                parsed = parsedate_to_datetime(value)
                return max(parsed.timestamp(), 0.0)
            except (TypeError, ValueError, OverflowError):
                return 0.0
        return 0.0

    @staticmethod
    def _addresses(values: object) -> tuple[str, ...]:
        if isinstance(values, str):
            raw_values = (values,)
        elif isinstance(values, (tuple, list)):
            raw_values = tuple(str(value) for value in values)
        else:
            raw_values = ()
        return tuple(
            address
            for _display_name, address in getaddresses(raw_values)
            if address
        )

    @staticmethod
    def _fetch_row(result: Mapping, remote_uid: int) -> Mapping:
        row = result.get(remote_uid)
        if row is None:
            row = result.get(str(remote_uid))
        if row is None and result:
            row = next(iter(result.values()))
        if not isinstance(row, Mapping):
            raise ValueError("IMAP response row is invalid")
        return row

    @staticmethod
    def _key_text(value: object) -> str:
        if isinstance(value, bytes):
            try:
                return decode_imap_utf7(value)
            except (UnicodeDecodeError, ValueError):
                return value.decode("utf-8", errors="replace")
        return str(value)

    @classmethod
    def _text_values(cls, value: object) -> tuple[str, ...]:
        if isinstance(value, (bytes, str)):
            values = (value,)
        elif isinstance(value, (list, tuple, set, frozenset)):
            values = tuple(value)
        else:
            values = ()
        return tuple(
            cls._key_text(item)
            for item in values
            if cls._key_text(item)
        )

    @classmethod
    def _remote_version(
        cls,
        row: Mapping,
        flags: tuple[str, ...],
        mailbox_keys: tuple[str, ...],
    ) -> str:
        modseq = row.get(b"MODSEQ", row.get("MODSEQ", ()))
        if isinstance(modseq, (tuple, list)) and modseq:
            modseq_value = str(modseq[0])
        else:
            modseq_value = str(modseq or "")
        payload = json.dumps(
            {
                "flags": sorted(flags),
                "mailboxes": sorted(mailbox_keys),
                "modseq": modseq_value,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _terminal_remote_version(command: RemoteOperationCommand) -> str:
        payload = json.dumps(
            {
                "action": command.remote_action,
                "idempotency_key": command.idempotency_key,
                "expected_remote_version": command.expected_remote_version,
                "target_native_key": command.target_native_key,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @classmethod
    def _append_uid(cls, result: object) -> int | None:
        if isinstance(result, bool):
            return None
        if isinstance(result, int):
            return result if result > 0 else None
        if isinstance(result, bytes):
            text = result.decode("ascii", errors="ignore")
        elif isinstance(result, str):
            text = result
        else:
            text = ""
        if text:
            match = re.search(
                r"\bAPPENDUID\s+[0-9]+\s+([0-9]+)\b",
                text,
                re.IGNORECASE,
            )
            if match:
                return int(match.group(1))
            return None
        if isinstance(result, Mapping):
            values = tuple(result.values())
        elif isinstance(result, (tuple, list)):
            values = tuple(result)
        else:
            values = ()
        for item in reversed(values):
            value = cls._append_uid(item)
            if value is not None:
                return value
        return None


__all__ = [
    "DatabaseProviderStateStore",
    "ProductionProviderRuntime",
    "ProviderStateStore",
    "RuntimeRemoteLocator",
]
