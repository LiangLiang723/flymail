"""Deterministic RFC822 composition and narrow SMTP delivery contracts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage, Message
from email.policy import SMTP, SMTPUTF8
from email.utils import format_datetime, formataddr, parseaddr
from typing import Protocol


_RECIPIENT_KINDS = {"to", "cc", "bcc"}


def _required_text(value: str, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    return normalized


def validate_mailbox_address(value: str, label: str = "address") -> str:
    normalized = _required_text(value, label)
    display_name, parsed = parseaddr(normalized)
    if display_name or parsed != normalized:
        parsed = parsed.strip()
    if not parsed or parsed.count("@") != 1:
        raise ValueError(f"{label} must be an email address")
    local, domain = parsed.rsplit("@", 1)
    if not local or not domain or any(character.isspace() for character in parsed):
        raise ValueError(f"{label} must be an email address")
    return parsed


def _message_id(value: str) -> str:
    normalized = _required_text(value, "message_id_header")
    if not (normalized.startswith("<") and normalized.endswith(">") and "@" in normalized):
        raise ValueError("message_id_header must be a bracketed Message-ID")
    return normalized


def _has_non_ascii(value: str) -> bool:
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return True
    return False


def _format_mailbox(display_name: str, address: str) -> str:
    if _has_non_ascii(address):
        return f"{display_name} <{address}>" if display_name else address
    return formataddr((display_name, address), charset="utf-8")


@dataclass(frozen=True, slots=True)
class SendRecipient:
    kind: str
    address: str
    display_name: str = ""

    def __post_init__(self) -> None:
        kind = str(self.kind or "").strip().casefold()
        if kind not in _RECIPIENT_KINDS:
            raise ValueError("recipient kind must be to, cc, or bcc")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(
            self,
            "address",
            validate_mailbox_address(self.address, "recipient address"),
        )
        object.__setattr__(self, "display_name", str(self.display_name or "").strip())

    @property
    def formatted(self) -> str:
        return _format_mailbox(self.display_name, self.address)


@dataclass(frozen=True, slots=True)
class ComposedAttachment:
    filename: str
    content_type: str
    content: bytes

    def __post_init__(self) -> None:
        filename = _required_text(self.filename, "attachment filename")
        content_type = str(self.content_type or "application/octet-stream").strip().casefold()
        if content_type.count("/") != 1:
            raise ValueError("attachment content_type must contain one slash")
        if not isinstance(self.content, bytes):
            raise TypeError("attachment content must be bytes")
        object.__setattr__(self, "filename", filename)
        object.__setattr__(self, "content_type", content_type)


@dataclass(frozen=True, slots=True)
class SendCommand:
    draft_id: str
    message_id_header: str
    created_at: float
    from_address: str
    from_display_name: str
    reply_to: str
    recipients: tuple[SendRecipient, ...]
    subject: str
    text_body: str
    html_body: str = ""
    attachments: tuple[ComposedAttachment, ...] = ()
    in_reply_to: str = ""
    references: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "draft_id", _required_text(self.draft_id, "draft_id"))
        object.__setattr__(self, "message_id_header", _message_id(self.message_id_header))
        object.__setattr__(self, "created_at", float(self.created_at))
        object.__setattr__(
            self,
            "from_address",
            validate_mailbox_address(self.from_address, "from_address"),
        )
        object.__setattr__(self, "from_display_name", str(self.from_display_name or "").strip())
        reply_to = str(self.reply_to or "").strip()
        if reply_to:
            reply_to = validate_mailbox_address(reply_to, "reply_to")
        object.__setattr__(self, "reply_to", reply_to)
        recipients = tuple(self.recipients)
        if not recipients or not any(recipient.kind == "to" for recipient in recipients):
            raise ValueError("at least one To recipient is required")
        if any(not isinstance(recipient, SendRecipient) for recipient in recipients):
            raise TypeError("recipients must contain SendRecipient values")
        addresses = [recipient.address.casefold() for recipient in recipients]
        if len(addresses) != len(set(addresses)):
            raise ValueError("recipient addresses must be unique")
        object.__setattr__(self, "recipients", recipients)
        object.__setattr__(self, "subject", str(self.subject or ""))
        object.__setattr__(self, "text_body", str(self.text_body or ""))
        object.__setattr__(self, "html_body", str(self.html_body or ""))
        attachments = tuple(self.attachments)
        if any(not isinstance(attachment, ComposedAttachment) for attachment in attachments):
            raise TypeError("attachments must contain ComposedAttachment values")
        object.__setattr__(self, "attachments", attachments)
        in_reply_to = str(self.in_reply_to or "").strip()
        if in_reply_to:
            in_reply_to = _message_id(in_reply_to)
        object.__setattr__(self, "in_reply_to", in_reply_to)
        references = tuple(_message_id(reference) for reference in self.references if str(reference).strip())
        object.__setattr__(self, "references", references)


@dataclass(frozen=True, slots=True)
class ComposedMessage:
    message_id_header: str
    envelope_from: str
    envelope_recipients: tuple[str, ...]
    source: bytes
    requires_smtp_utf8: bool


@dataclass(frozen=True, slots=True)
class SmtpSendRequest:
    account_id: str
    message_id_header: str
    envelope_from: str
    envelope_recipients: tuple[str, ...]
    source: bytes
    use_smtp_utf8: bool


@dataclass(frozen=True, slots=True)
class SentVerificationRequest:
    account_id: str
    message_id_header: str
    started_at: float
    recipients: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SentAppendRequest:
    account_id: str
    message_id_header: str
    source: bytes


@dataclass(frozen=True, slots=True)
class SmtpSendResult:
    response_code: int
    safe_response: str = ""

    def __post_init__(self) -> None:
        code = int(self.response_code)
        if not 200 <= code <= 299:
            raise ValueError("SMTP send result must be an accepted 2xx response")
        object.__setattr__(self, "response_code", code)
        object.__setattr__(self, "safe_response", str(self.safe_response or "")[:512])


@dataclass(frozen=True, slots=True)
class SentVerificationResult:
    found: bool
    remote_uid: int | None = None
    provider_message_id: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.found, bool):
            raise TypeError("found must be bool")
        if self.remote_uid is not None and int(self.remote_uid) < 1:
            raise ValueError("remote_uid must be positive")
        object.__setattr__(self, "remote_uid", int(self.remote_uid) if self.remote_uid is not None else None)
        object.__setattr__(self, "provider_message_id", str(self.provider_message_id or "")[:191])


@dataclass(frozen=True, slots=True)
class SentAppendResult:
    remote_uid: int | None = None

    def __post_init__(self) -> None:
        if self.remote_uid is not None and int(self.remote_uid) < 1:
            raise ValueError("remote_uid must be positive")
        object.__setattr__(self, "remote_uid", int(self.remote_uid) if self.remote_uid is not None else None)


class SmtpDeliveryUncertain(RuntimeError):
    """The connection ended after DATA and server acceptance is unknown."""


class SmtpMailGateway(Protocol):
    async def send(self, request: SmtpSendRequest) -> SmtpSendResult: ...

    async def verify_sent(self, request: SentVerificationRequest) -> SentVerificationResult: ...

    async def append_sent_copy(self, request: SentAppendRequest) -> SentAppendResult: ...


class MimeComposer:
    @staticmethod
    def compose(command: SendCommand) -> ComposedMessage:
        if not isinstance(command, SendCommand):
            raise TypeError("command must be SendCommand")
        requires_smtp_utf8 = any(
            _has_non_ascii(value)
            for value in (
                command.from_address,
                *(recipient.address for recipient in command.recipients),
            )
        )
        message_policy = SMTPUTF8 if requires_smtp_utf8 else SMTP
        message = EmailMessage(policy=message_policy)
        message["Message-ID"] = command.message_id_header
        message["Date"] = format_datetime(datetime.fromtimestamp(command.created_at, UTC))
        message["From"] = _format_mailbox(
            command.from_display_name,
            command.from_address,
        )
        if command.reply_to:
            message["Reply-To"] = command.reply_to
        for kind, header in (("to", "To"), ("cc", "Cc")):
            values = [recipient.formatted for recipient in command.recipients if recipient.kind == kind]
            if values:
                message[header] = ", ".join(values)
        message["Subject"] = command.subject
        if command.in_reply_to:
            message["In-Reply-To"] = command.in_reply_to
        if command.references:
            message["References"] = " ".join(command.references)

        text_body = command.text_body or ""
        message.set_content(text_body)
        if command.html_body:
            message.add_alternative(command.html_body, subtype="html")
        for attachment in command.attachments:
            major, subtype = attachment.content_type.split("/", 1)
            message.add_attachment(
                attachment.content,
                maintype=major,
                subtype=subtype,
                filename=attachment.filename,
            )
        MimeComposer._set_deterministic_boundaries(
            message,
            seed=command.message_id_header,
            path="0",
        )
        source = message.as_bytes(policy=message_policy)
        envelope_recipients = tuple(recipient.address for recipient in command.recipients)
        return ComposedMessage(
            message_id_header=command.message_id_header,
            envelope_from=command.from_address,
            envelope_recipients=envelope_recipients,
            source=source,
            requires_smtp_utf8=requires_smtp_utf8,
        )

    @staticmethod
    def _set_deterministic_boundaries(message: Message, *, seed: str, path: str) -> None:
        if not message.is_multipart():
            return
        digest = hashlib.sha256(f"{seed}:{path}".encode("utf-8")).hexdigest()[:32]
        message.set_boundary(f"flymail-{digest}")
        payload = message.get_payload()
        if isinstance(payload, list):
            for index, child in enumerate(payload):
                MimeComposer._set_deterministic_boundaries(
                    child,
                    seed=seed,
                    path=f"{path}.{index}",
                )
