"""Shared helpers: logging, ids, url/text normalization, amount extraction."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "fbclid", "gclid", "mc_cid", "mc_eid", "ref_src", "spm",
}


def log(stage: str, msg: str) -> None:
    """Log line labeled with the pipeline stage (春 scout / 夏 scorer / 秋 executor)."""
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{stage}] {now} {msg}", flush=True)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_url(url: str) -> str:
    """Strip tracking params/fragment so the same opportunity dedupes across sources."""
    if not url:
        return ""
    try:
        parts = urlparse(url.strip())
    except ValueError:
        return url.strip()
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() not in TRACKING_PARAMS]
    path = parts.path.rstrip("/") or "/"
    return urlunparse((parts.scheme.lower() or "https", host, path, "", urlencode(query), ""))


def item_id(url: str, title: str = "") -> str:
    basis = canonical_url(url) or title.strip().lower()
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = _TAG_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def title_fingerprint(title: str) -> str:
    """ASCII-folded, punctuation-free title prefix for cross-source fuzzy dedupe."""
    text = unicodedata.normalize("NFKD", title or "")
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]", "", text)[:80]


_AMOUNT_RE = re.compile(
    r"(?P<cur>[$€£])?\s?(?P<num>\d[\d,]*(?:\.\d+)?)\s?(?P<mult>[kKmM])?\s?"
    r"(?P<unit>USD|USDT|USDC|EUR|GBP|dollars?)?(?![\w])",
    re.IGNORECASE,
)

_FX_TO_USD = {"€": 1.08, "£": 1.27, "EUR": 1.08, "GBP": 1.27}

# Amounts in these contexts are costs (spend/deposit requirements), not prizes:
# "$120k Total Spend Required" (after) or "Spend $25" (before).
_COST_AFTER_RE = re.compile(
    r"^\W*(?:[\w'-]+\W+){0,2}(spend|spending|deposit|purchase|stake|staked|wager)",
    re.IGNORECASE,
)
_COST_BEFORE_RE = re.compile(
    r"(spend|spending|deposit|depositing|purchase|stake|staked|wager|costs?)\W{0,6}$",
    re.IGNORECASE,
)


def extract_amounts_usd(text: str) -> list[float]:
    """Best-effort USD prize amounts mentioned in free text."""
    if not text:
        return []
    out: list[float] = []
    for m in _AMOUNT_RE.finditer(text):
        cur, unit = m.group("cur") or "", (m.group("unit") or "").upper()
        if not cur and unit not in {"USD", "USDT", "USDC", "EUR", "GBP"}:
            continue
        after = text[m.end():m.end() + 30]
        before = text[max(0, m.start() - 25):m.start()]
        if _COST_AFTER_RE.match(after) or _COST_BEFORE_RE.search(before):
            continue
        try:
            num = float(m.group("num").replace(",", ""))
        except ValueError:
            continue
        mult = (m.group("mult") or "").lower()
        if mult == "k":
            num *= 1_000
        elif mult == "m":
            num *= 1_000_000
        if num < 1 or num > 100_000_000:
            continue
        fx = _FX_TO_USD.get(cur) or _FX_TO_USD.get(unit) or 1.0
        out.append(num * fx)
    return out
