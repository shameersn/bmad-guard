# /// script
# requires-python = ">=3.10"
# ///
"""Built-in rules for the local prompt check (ADR 0006). Pattern matching only; nothing leaves the machine.

Categories:
  secrets    well-known public key formats, credential assignments, connection strings, high-entropy tokens
  phi        personal / health identifiers, with checksums where the format has one (Luhn, mod 11, Verhoeff)
  injection  prompt-injection phrasing, ported from PromptGateway's injection patterns (recorded only)
  size       token estimate (chars / 4), see prompt_check.py

The secret formats are written from public vendor documentation, not copied from a rule pack.
A finding is (category, rule_id) only: the matched text never leaves `scan()`.
"""

from __future__ import annotations

import base64
import binascii
import math
import re
from collections.abc import Callable
from dataclasses import dataclass

CATEGORIES = ("secrets", "phi", "injection")

# Obvious fakes. A match is skipped when it contains one of these (case-insensitive), as typed or with
# spaces and dashes removed. "example" covers the AWS documentation keys, example.com and friends.
BUILTIN_ALLOW = (
    "example",
    "sk_test_",
    "pk_test_",
    "rk_test_",
    "@localhost",
    "@127.0.0.1",
    ".invalid",
    "noreply@",
    "no-reply@",
    "000-00-0000",
    "123-45-6789",
    "078-05-1120",  # the SSN printed on a 1938 wallet insert; widely published, never valid
    "4111111111111111",  # public card-network test numbers
    "4242424242424242",
    "5555555555554444",
    "378282246310005",
    "555-01",  # US numbers reserved for fiction: 555-0100 to 555-0199
)

# Placeholder values that are not secrets: `password: string`, `api_key = <your key>`, `pwd=${PWD}`.
PLACEHOLDER_VALUES = {
    "string",
    "str",
    "number",
    "int",
    "bool",
    "boolean",
    "null",
    "none",
    "nil",
    "undefined",
    "true",
    "false",
    "password",
    "passwd",
    "secret",
    "changeme",
    "redacted",
    "required",
    "optional",
    "dummy",
    "placeholder",
    "your_password",
    "yourpassword",
}
PLACEHOLDER_START = ("$", "{", "<", "%", "[", "(", "*", "os.", "process.", "env", "settings.", "config.", "self.")

# --- checksums -------------------------------------------------------------------------------------


def digits_of(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def luhn_valid(number: str) -> bool:
    digits = [int(d) for d in digits_of(number)]
    if len(digits) < 2:
        return False
    total = 0
    for i, digit in enumerate(reversed(digits)):
        if i % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def nhs_valid(number: str) -> bool:
    """NHS number: 10 digits; the last is a mod-11 check digit over weights 10..2."""
    digits = [int(d) for d in digits_of(number)]
    if len(digits) != 10:
        return False
    remainder = sum(d * w for d, w in zip(digits[:9], range(10, 1, -1), strict=True)) % 11
    check = 11 - remainder
    if check == 11:
        check = 0
    return check != 10 and check == digits[9]


_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def verhoeff_valid(number: str) -> bool:
    check = 0
    for i, ch in enumerate(reversed(digits_of(number))):
        check = _VERHOEFF_D[check][_VERHOEFF_P[i % 8][int(ch)]]
    return check == 0


def shannon_entropy(text: str) -> float:
    """Bits per character."""
    if not text:
        return 0.0
    counts: dict[str, int] = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    size = len(text)
    return -sum(n / size * math.log2(n / size) for n in counts.values())


# --- rule plumbing ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Rule:
    rule_id: str
    category: str
    regex: re.Pattern
    check: Callable[[re.Match, str], bool] | None = None  # extra validation: (match, whole text) -> keep?


def _context(match: re.Match, text: str, words: re.Pattern, before: int = 40) -> bool:
    return bool(words.search(text[max(0, match.start() - before) : match.end() + 10]))


def _has_separator(match: re.Match) -> bool:
    return any(ch in " -" for ch in match.group(0))


def real_value(value: str, min_len: int = 6) -> bool:
    """False for placeholders, variables and code; True for something that may be a real secret."""
    value = value.strip().strip("'\"`")
    low = value.lower()
    if len(value) < min_len or low in PLACEHOLDER_VALUES or low.startswith(PLACEHOLDER_START):
        return False
    if set(value) <= {"*", "x", "X", "."}:
        return False
    if "(" in value or re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)+", value):  # a call or attribute access
        return False
    return not re.fullmatch(r"[A-Z][A-Z0-9_]*_[A-Z0-9_]+", value)  # an ENV_VAR_NAME


# Digit runs: not glued to other digits, also not across one space or dash (so a part of a longer
# grouped number does not match on its own).
_NB = r"(?<!\d)(?<!\d[ -])"
_NA = r"(?![ -]?\d)"

_CARD_IIN = re.compile(r"4|5[1-5]|2[2-7]|3[47]|6011|65|64[4-9]|35")
_NHS_WORDS = re.compile(r"(?i)\bnhs\b")
_AADHAAR_WORDS = re.compile(r"(?i)\b(aadhaa?r|uidai|uid)\b")


def _card(match: re.Match, text: str) -> bool:
    digits = digits_of(match.group(0))
    return 13 <= len(digits) <= 19 and bool(_CARD_IIN.match(digits)) and luhn_valid(digits)


def _nhs(match: re.Match, text: str) -> bool:
    return nhs_valid(match.group(0)) and (_has_separator(match) or _context(match, text, _NHS_WORDS))


def _aadhaar(match: re.Match, text: str) -> bool:
    return verhoeff_valid(match.group(0)) and (_has_separator(match) or _context(match, text, _AADHAAR_WORDS))


def _intl_phone(match: re.Match, text: str) -> bool:
    return 9 <= len(digits_of(match.group(0))) <= 15


def _assignment(match: re.Match, text: str) -> bool:
    return real_value(match.group("value"))


def _connection(match: re.Match, text: str) -> bool:
    return real_value(match.group("value"), min_len=3)


def _email(match: re.Match, text: str) -> bool:
    return match.group(0).split("@", 1)[0].lower() != "git"  # git@github.com:org/repo.git


def _base64_instruction(match: re.Match, text: str) -> bool:
    candidate = match.group(0)
    try:
        decoded = base64.b64decode(candidate + "=" * (-len(candidate) % 4), validate=True)
    except (binascii.Error, ValueError):
        return False
    words = decoded.decode("utf-8", errors="ignore").lower()
    return any(k in words for k in ("ignore", "system prompt", "instruction", "override", "jailbreak"))


def _r(rule_id: str, category: str, pattern: str, check=None, flags: int = 0) -> Rule:
    return Rule(rule_id, category, re.compile(pattern, flags), check)


SECRET_RULES = (
    _r("secret.aws-access-key-id", "secrets", r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b"),
    _r(
        "secret.aws-secret-access-key",
        "secrets",
        r"(?i)\baws_?secret_?access_?key\b[\"']?\s*[:=]\s*[\"']?(?P<value>[A-Za-z0-9/+=]{40})(?![A-Za-z0-9/+=])",
        _assignment,
    ),
    _r("secret.github-token", "secrets", r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b"),
    _r("secret.github-fine-grained-pat", "secrets", r"\bgithub_pat_[A-Za-z0-9_]{60,255}\b"),
    _r("secret.gitlab-token", "secrets", r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    _r("secret.slack-token", "secrets", r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    _r("secret.slack-webhook", "secrets", r"https://hooks\.slack\.com/(?:services|workflows)/[A-Za-z0-9_/]{20,}"),
    _r("secret.stripe-live-key", "secrets", r"\b(?:sk|rk|pk)_live_[A-Za-z0-9]{20,}\b"),
    _r("secret.google-api-key", "secrets", r"\bAIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])"),
    _r("secret.openai-api-key", "secrets", r"\bsk-(?:proj|svcacct|admin)-[A-Za-z0-9_-]{40,}"),
    _r("secret.anthropic-api-key", "secrets", r"\bsk-ant-[a-z]{3,6}\d{2}-[A-Za-z0-9_-]{60,}"),
    _r("secret.npm-token", "secrets", r"\bnpm_[A-Za-z0-9]{36}\b"),
    _r("secret.private-key", "secrets", r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"),
    _r("secret.jwt", "secrets", r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    _r(
        "secret.connection-string",
        "secrets",
        r"(?i)\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|rediss?|amqps?|mssql|sqlserver|s?ftp|smtps?|ldaps?)"
        r"://[^\s:/@]+:(?P<value>[^\s/@]+)@[^\s/?#]+",
        _connection,
    ),
    _r(
        "secret.credential-assignment",
        "secrets",
        r"(?i)\b(?:password|passwd|pwd|secret|client_?secret|api_?key|apikey|access_?token|auth_?token|secret_?key)"
        r"[\"']?\s*(?::|=|:=|=>)\s*(?P<value>\"[^\"\n]{6,}\"|'[^'\n]{6,}'|[^\s\"',;]{6,})",
        _assignment,
    ),
)

PHI_RULES = (
    _r("phi.card-number", "phi", _NB + r"(?:\d[ -]?){12,18}\d" + _NA, _card),
    _r("phi.nhs-number", "phi", _NB + r"\d{3}([ -]?)\d{3}\1\d{4}" + _NA, _nhs),
    _r("phi.aadhaar-number", "phi", _NB + r"[2-9]\d{3}([ -]?)\d{4}\1\d{4}" + _NA, _aadhaar),
    _r("phi.us-ssn", "phi", r"(?<![\d-])(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?![\d-])"),
    _r("phi.email", "phi", r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b", _email),
    _r("phi.phone-us", "phi", r"(?<![\w+])(?:\+?1[ .-]?)?(?:\(\d{3}\)\s?|\d{3}[ .-])\d{3}[ .-]\d{4}(?![\d-])"),
    _r("phi.phone-intl", "phi", r"(?<![\w+])\+[2-9]\d{0,2}(?:[ -]?\d{2,5}){2,4}(?![\d-])", _intl_phone),
)

# Ported from PromptGateway policy.yaml `injection.patterns`, tightened where plain developer text
# matched (`act as`, `### System`, `show ... instructions`, a bare `dan`). Recorded only (ADR 0006).
INJECTION_RULES = (
    _r(
        "injection.ignore-instructions",
        "injection",
        r"(?i)\b(?:ignore|disregard|forget)\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\s+"
        r"(?:instructions?|prompts?|rules?|restrictions?)\b",
    ),
    _r(
        "injection.system-prompt-override",
        "injection",
        r"(?i)\byou\s+are\s+now\s+(?:an?\s+)?(?:unrestricted|unfiltered|jailbroken|evil|dan)\b|\bnew\s+system\s+prompt\b"
        r"|\bact\s+as\s+(?:if\s+you\s+(?:are|were|have)\s+no\s+(?:restrictions|rules|filters)"
        r"|an?\s+(?:unrestricted|unfiltered|uncensored|jailbroken)\b)",
    ),
    _r("injection.dan-jailbreak", "injection", r"\bDAN\b|(?i:\bdo\s+anything\s+now\b|\bjailbreak(?:ed)?\s+mode\b)"),
    _r(
        "injection.role-escape",
        "injection",
        r"(?i)\bpretend\s+(?:that\s+)?(?:you\s+)?(?:are|to\s+be)\b.{0,40}\bno\s+(?:restrictions|rules|filters)\b",
    ),
    _r(
        "injection.delimiter-confusion",
        "injection",
        r"(?im)\[/?(?:system|assistant)\]|^\s*###\s*(?:system|instruction)s?\s*:?\s*$|<\|(?:im_start|im_end|system|endoftext)\|>",
    ),
    _r(
        "injection.reveal-system-prompt",
        "injection",
        r"(?i)\b(?:reveal|show|print|repeat|output)\b.{0,25}\b(?:(?:your|the)\s+system\s+prompt|your\s+(?:hidden\s+)?"
        r"(?:instructions|guidelines))\b",
    ),
    _r("injection.base64-instruction", "injection", r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{40,}={0,2}", _base64_instruction),
    _r(
        "injection.unrestricted-ai",
        "injection",
        r"(?i)\b(?:unrestricted|unfiltered|uncensored)\s+ai\b",
    ),
    _r(
        "injection.bypass-guardrails",
        "injection",
        r"(?i)\b(?:ignore|bypass|override|disable)\s+(?:all\s+)?(?:your\s+)?(?:guardrails?|safety\s+(?:rules|filters)"
        r"|content\s+filters?|restrictions)\b",
    ),
)

BUILTIN_RULES = SECRET_RULES + PHI_RULES + INJECTION_RULES

# High-entropy tokens (secrets category). A candidate is 32-200 characters of [A-Za-z0-9+=_-] with
# upper case, lower case and at least 2 digits. Skipped: pure hex (git SHAs, hashes, UUIDs), base64
# that decodes to readable text with spaces, and anything below the entropy bar. The bar is
# log2(length) - 0.9 bits per character, capped at 4.3: random base62 of 32 characters scores about
# 4.2-4.6, long CamelCase identifiers about 4.0-4.3 (and rarely carry 2 digits).
ENTROPY_RULE_ID = "secret.high-entropy"
ENTROPY_MIN_LEN = 32
ENTROPY_MAX_LEN = 200
ENTROPY_MAX_BAR = 4.3
ENTROPY_MIN_DIGITS = 2
_TOKEN_RE = re.compile(r"[A-Za-z0-9+=_-]+")
_HEX_RE = re.compile(r"[0-9a-fA-F-]+")


def entropy_bar(length: int) -> float:
    return min(ENTROPY_MAX_BAR, math.log2(length) - 0.9)


def _readable_base64(token: str) -> bool:
    try:
        decoded = base64.b64decode(token + "=" * (-len(token) % 4), validate=True).decode("utf-8")
    except (binascii.Error, ValueError):
        return False
    printable = sum(ch.isprintable() for ch in decoded)
    return " " in decoded and printable >= 0.95 * len(decoded)


def high_entropy(token: str) -> bool:
    core = token.strip("=-_")
    if not ENTROPY_MIN_LEN <= len(core) <= ENTROPY_MAX_LEN or _HEX_RE.fullmatch(core):
        return False
    if not (any(c.isupper() for c in core) and any(c.islower() for c in core)):
        return False
    if sum(c.isdigit() for c in core) < ENTROPY_MIN_DIGITS or _readable_base64(core):
        return False
    return shannon_entropy(core) >= entropy_bar(len(core))


# --- scan ------------------------------------------------------------------------------------------


def allowed(matched: str, allow: tuple[str, ...]) -> bool:
    low = matched.lower()
    squeezed = re.sub(r"[ -]", "", low)
    return any(a in low or a.replace("-", "").replace(" ", "") in squeezed for a in allow)


def _overlaps(span: tuple[int, int], spans: list[tuple[int, int]]) -> bool:
    return any(s < span[1] and span[0] < e for s, e in spans)


def scan(text: str, extra_rules: tuple[Rule, ...] = (), allow: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """Return one (category, rule_id) per match. Never returns or stores the matched text."""
    allow_list = tuple(a.lower() for a in BUILTIN_ALLOW + tuple(allow) if a)
    found: list[tuple[str, str]] = []
    secret_spans: list[tuple[int, int]] = []
    for rule in BUILTIN_RULES + tuple(extra_rules):
        for match in rule.regex.finditer(text):
            if rule.check and not rule.check(match, text):
                continue
            if allowed(match.group(0), allow_list):
                continue
            if rule.category == "phi" and _overlaps(match.span(), secret_spans):
                continue  # e.g. "user:pw@host" inside a connection string is not an email address
            found.append((rule.category, rule.rule_id))
            if rule.category == "secrets":
                secret_spans.append(match.span())
    for match in _TOKEN_RE.finditer(text):
        if _overlaps(match.span(), secret_spans):
            continue  # already reported by a named rule
        if high_entropy(match.group(0)) and not allowed(match.group(0), allow_list):
            found.append(("secrets", ENTROPY_RULE_ID))
    return found


def estimate_tokens(text: str) -> int:
    """Rough token count (about 4 characters per token for English); tiktoken is not in the stdlib."""
    return math.ceil(len(text) / 4)
