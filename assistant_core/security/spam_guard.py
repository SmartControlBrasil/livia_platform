import re
from dataclasses import dataclass

from django.conf import settings

_LINK_RE = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
_ALPHA_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9]")
_REPEAT_RE = re.compile(r"(.)\1{12,}")
_SPAM_TERM_RE = re.compile(
    r"\b(?:casino|bet|crypto spam|free money|loan|payday loan|adult|porn|xxx|viagra|make money fast)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SpamCheckResult:
    is_spam: bool
    reason: str = ""


def check_message_spam(message):
    if not getattr(settings, "LIVIA_SPAM_GUARD_ENABLED", True):
        return SpamCheckResult(False)

    text = str(message or "")
    lowered = text.lower()
    link_count = len(_LINK_RE.findall(text))
    if link_count >= 3:
        return SpamCheckResult(True, "too_many_links")

    if _SPAM_TERM_RE.search(lowered):
        return SpamCheckResult(True, "spam_term")

    if _REPEAT_RE.search(text):
        return SpamCheckResult(True, "repeated_characters")

    if len(text) >= 40:
        useful_chars = len(_ALPHA_RE.findall(text))
        special_chars = sum(1 for char in text if not char.isspace() and not _ALPHA_RE.match(char))
        if useful_chars < 12 and special_chars > useful_chars * 2:
            return SpamCheckResult(True, "low_signal_special_chars")

    return SpamCheckResult(False)
