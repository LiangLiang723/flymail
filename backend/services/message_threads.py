import hashlib
import re


_MESSAGE_ID_RE = re.compile(r"<[^<>\s]+>")
_THREAD_PREFIX_RE = re.compile(r"^\s*(?:(?:re|fw|fwd)\s*:\s*)+", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_message_id(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = _MESSAGE_ID_RE.search(text)
    if match:
        return match.group(0)
    return f"<{text.strip('<>')}>"


def extract_reference_ids(value: str | None) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    matches = _MESSAGE_ID_RE.findall(text)
    if matches:
        return [normalize_message_id(item) for item in matches if normalize_message_id(item)]
    return [normalize_message_id(item) for item in text.split() if normalize_message_id(item)]


def normalize_subject_for_thread(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    previous = None
    while previous != text:
        previous = text
        text = _THREAD_PREFIX_RE.sub("", text).strip()
    return _WHITESPACE_RE.sub(" ", text).casefold()


def _hashed_thread_key(kind: str, account_id: str, identity: str) -> str:
    raw = f"{account_id}\0{identity}".encode("utf-8")
    return f"{kind}:{hashlib.sha256(raw).hexdigest()}"


def build_thread_key(
    account_id: str,
    message_id: str | None,
    in_reply_to: str | None,
    references: str | None,
    subject: str | None,
) -> str:
    reference_ids = extract_reference_ids(references)
    root_id = reference_ids[0] if reference_ids else normalize_message_id(in_reply_to)
    if not root_id:
        root_id = normalize_message_id(message_id)
    if root_id:
        return _hashed_thread_key("rfc", account_id, root_id.casefold())

    normalized_subject = normalize_subject_for_thread(subject)
    if normalized_subject:
        return _hashed_thread_key("subject", account_id, normalized_subject)
    return ""
