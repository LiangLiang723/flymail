from dataclasses import dataclass
from datetime import date
import shlex


@dataclass
class ParsedMessageSearch:
    free_text: str = ""
    from_addr: str = ""
    to_addr: str = ""
    subject: str = ""
    after: str = ""
    before: str = ""
    read_state: str = ""
    has_attachment: bool = False
    starred: bool = False


def _valid_iso_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def parse_message_search(query: str) -> ParsedMessageSearch:
    parsed = ParsedMessageSearch()
    free_tokens: list[str] = []

    try:
        tokens = shlex.split(query or "")
    except ValueError:
        tokens = str(query or "").split()

    for token in tokens:
        lower = token.lower()
        if lower.startswith("from:") and token[5:]:
            parsed.from_addr = token[5:]
        elif lower.startswith("to:") and token[3:]:
            parsed.to_addr = token[3:]
        elif lower.startswith("subject:") and token[8:]:
            parsed.subject = token[8:]
        elif lower.startswith("after:") and _valid_iso_date(token[6:]):
            parsed.after = token[6:]
        elif lower.startswith("before:") and _valid_iso_date(token[7:]):
            parsed.before = token[7:]
        elif lower == "has:attachment":
            parsed.has_attachment = True
        elif lower == "is:unread":
            parsed.read_state = "unread"
        elif lower == "is:read":
            parsed.read_state = "read"
        elif lower == "is:starred":
            parsed.starred = True
        else:
            free_tokens.append(token)

    parsed.free_text = " ".join(free_tokens).strip()
    return parsed
