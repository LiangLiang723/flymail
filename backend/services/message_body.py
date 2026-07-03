import re
from html import unescape


_EMPTY_BLOCK_RE = re.compile(
    r"<(?P<tag>p|div)(?P<attrs>[^>]*)>\s*(?:(?:&nbsp;|\u00a0|<br\b[^>]*>)\s*)*</(?P=tag)\s*>",
    flags=re.IGNORECASE,
)


def prepare_outgoing_body_html(body_html: str) -> str:
    if not body_html:
        return ""

    def replace_empty_block(match: re.Match) -> str:
        return f"<{match.group('tag')}{match.group('attrs')}><br></{match.group('tag')}>"

    return _EMPTY_BLOCK_RE.sub(replace_empty_block, body_html)


def html_to_text(body_html: str) -> str:
    body_html = prepare_outgoing_body_html(body_html)
    if not body_html:
        return ""
    text = re.sub(r"<(p|div)([^>]*)><br\b[^>]*></\1\s*>", "\n", body_html, flags=re.IGNORECASE)
    text = re.sub(r"<br\b[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|h[1-6]|li|tr)\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li\b[^>]*>", "- ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    return text.strip()
