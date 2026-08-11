"""Context-aware verification-code extraction for cached email content.

The extractor is intentionally deterministic and dependency-free.  It only
returns numeric 4-8 digit codes when nearby text strongly suggests an OTP or
verification flow, reducing false positives from dates, phone numbers, order
IDs, invoice IDs, and similar numbers commonly found in mail.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser


_NEIGHBORHOOD = 80

_STRONG_POSITIVE_RE = re.compile(
    r"(?:"
    r"验证码|验证代码|校验码|动态码|动态密码|一次性密码|安全码|认证码|登录码|确认码|"
    r"验证(?:自己|您|你)?的?身份|"
    r"\botp\b|\bpasscode\b|\bverification(?:\s+code)?\b|"
    r"\bsecurity\s+code\b|\bauthentication\s+code\b|"
    r"\bauth(?:entication)?\s+code\b|\blogin\s+code\b|"
    r"\bsign[- ]?in\s+code\b|\bconfirmation\s+code\b|"
    r"\bconfirm\s+code\b|\bone[- ]?time(?:\s+password|\s+code)?\b"
    r")",
    re.IGNORECASE,
)

_GENERIC_POSITIVE_RE = re.compile(
    r"(?:\b(?:your|the|access)\s+code\b|\bcode\s*(?:is|:|：)|\bpin\b)",
    re.IGNORECASE,
)

_NEGATIVE_RE = re.compile(
    r"(?:"
    r"订单|订单号|发票|发票号|物流|运单|快递|电话号码?|手机号|金额|价格|"
    r"优惠|优惠券|折扣|邀请码|兑换码|"
    r"\border\b|\binvoice\b|\btracking\b|\bshipment\b|\bphone\b|\btel\b|"
    r"\bamount\b|\bprice\b|\btotal\b|\bcoupon\b|\bpromo\b|\bdiscount\b|"
    r"\breferral\b|\bvoucher\b|\bgift\s+code\b"
    r")",
    re.IGNORECASE,
)

_CANDIDATE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:\d{2,4}[ -]\d{2,4}|\d{4,8})(?![A-Za-z0-9])"
)
_DATE_RE = re.compile(
    r"(?<!\d)(?:(?:19|20)\d{2}[-/.]\d{1,2}(?:[-/.]\d{1,2})?|"
    r"\d{1,2}[-/.]\d{1,2}[-/.](?:19|20)\d{2})(?!\d)"
)
_NUMBERISH_RE = re.compile(r"(?<!\d)\+?\d[\d\s().-]{7,}\d(?!\d)")


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth and data:
            self.parts.append(data)


def _html_to_text(value: str) -> str:
    if not value:
        return ""
    parser = _VisibleTextParser()
    try:
        parser.feed(value)
        parser.close()
        return html.unescape(" ".join(parser.parts))
    except Exception:
        return html.unescape(re.sub(r"<[^>]+>", " ", value))


def _span_distance(left: tuple[int, int], right: tuple[int, int]) -> int:
    if left[1] < right[0]:
        return right[0] - left[1]
    if right[1] < left[0]:
        return left[0] - right[1]
    return 0


def _overlaps(span: tuple[int, int], blocked_spans: list[tuple[int, int]]) -> bool:
    return any(span[0] < blocked[1] and blocked[0] < span[1] for blocked in blocked_spans)


def _blocked_number_spans(text: str) -> list[tuple[int, int]]:
    blocked = [match.span() for match in _DATE_RE.finditer(text)]
    for match in _NUMBERISH_RE.finditer(text):
        if sum(char.isdigit() for char in match.group(0)) >= 9:
            blocked.append(match.span())
    return blocked


def _nearest_distance(span: tuple[int, int], matches: list[re.Match[str]]) -> int | None:
    if not matches:
        return None
    return min(_span_distance(span, match.span()) for match in matches)


def _extract_from_text(text: str) -> str:
    if not text:
        return ""

    strong_matches = list(_STRONG_POSITIVE_RE.finditer(text))
    generic_matches = list(_GENERIC_POSITIVE_RE.finditer(text))
    if not strong_matches and not generic_matches:
        return ""

    negative_matches = list(_NEGATIVE_RE.finditer(text))
    blocked_spans = _blocked_number_spans(text)
    ranked: list[tuple[int, int, str]] = []

    for candidate in _CANDIDATE_RE.finditer(text):
        span = candidate.span()
        if _overlaps(span, blocked_spans):
            continue

        normalized = re.sub(r"[ -]", "", candidate.group(0))
        if not 4 <= len(normalized) <= 8:
            continue

        strong_distance = _nearest_distance(span, strong_matches)
        generic_distance = _nearest_distance(span, generic_matches)
        use_strong = strong_distance is not None and strong_distance <= _NEIGHBORHOOD
        use_generic = generic_distance is not None and generic_distance <= _NEIGHBORHOOD
        if not use_strong and not use_generic:
            continue

        if use_strong and (not use_generic or strong_distance <= generic_distance):
            positive_distance = int(strong_distance or 0)
            score = 160 - positive_distance
            strong_context = True
        else:
            positive_distance = int(generic_distance or 0)
            score = 100 - positive_distance
            strong_context = False

        negative_distance = _nearest_distance(span, negative_matches)
        if negative_distance is not None and negative_distance <= 32:
            if negative_distance < positive_distance:
                score -= 90 if strong_context else 140
            else:
                score -= 25 if strong_context else 100

        if score >= 70:
            ranked.append((score, -span[0], normalized))

    if not ranked:
        return ""
    ranked.sort(reverse=True)
    return ranked[0][2]


def extract_verification_code(
    subject: str = "",
    body_text: str = "",
    body_html: str = "",
) -> str:
    """Return a normalized 4-8 digit verification code, or an empty string."""
    for source in (
        subject or "",
        body_text or "",
        _html_to_text(body_html or ""),
    ):
        code = _extract_from_text(source)
        if code:
            return code
    return ""
