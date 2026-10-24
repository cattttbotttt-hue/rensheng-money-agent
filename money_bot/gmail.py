"""Verification-mail helper via Gmail IMAP (optional; graceful no-op when unset).

Reads ONLY: never sends, never deletes. Env:
  GMAIL_ADDRESS       — a bot-dedicated Gmail (example: bot@example.com)
  GMAIL_APP_PASSWORD  — a 16-char Google *app password* (NOT the account
                        password; create under Google Account → Security →
                        2-Step Verification → App passwords). Local .env only.
"""

from __future__ import annotations

import email
import imaplib
import re
import urllib.request
from datetime import date, timedelta
from urllib.parse import urlparse

from . import config, ledger
from .util import log, utc_now_iso

STAGE = "秋"
IMAP_HOST = "imap.gmail.com"

_LINK_RE = re.compile(r"https?://[^\s<>')\]]+")
_CONFIRM_RE = re.compile(r"confirm|verif|activat|validat", re.IGNORECASE)


def _enabled() -> bool:
    config.load_dotenv()
    return bool(config.env("GMAIL_ADDRESS") and config.env("GMAIL_APP_PASSWORD"))


def _reg_domain(host: str) -> str:
    labels = (host or "").lower().split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else (host or "")


def extract_links(text: str, domain: str) -> list[str]:
    """Links mentioning the site domain or looking like a confirmation URL."""
    out: list[str] = []
    for raw in _LINK_RE.findall(text or ""):
        url = raw.rstrip(".,);]")
        host = urlparse(url).hostname or ""
        if host.endswith(domain) or _CONFIRM_RE.search(url):
            if url not in out:
                out.append(url)
    return out


def _message_text(msg) -> str:
    parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() in ("text/plain", "text/html"):
                try:
                    parts.append(part.get_payload(decode=True).decode("utf-8", "replace"))
                except Exception:
                    continue
    else:
        try:
            parts.append(msg.get_payload(decode=True).decode("utf-8", "replace"))
        except Exception:
            pass
    return "\n".join(parts)


def _fetch_status(url: str) -> int | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": config.USER_AGENT})
        with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as resp:
            return resp.status
    except Exception as exc:
        log(STAGE, f"驗證連結開啟失敗 verify link fetch failed: {str(exc)[:150]}")
        return None


def verify_item(item: dict, days: int = 2) -> dict | None:
    """Find a recent confirmation mail from the item's domain and open its link."""
    if not _enabled():
        log(STAGE, "GMAIL_ADDRESS/GMAIL_APP_PASSWORD 未設定 → verify 靜默略過 (graceful no-op)")
        return None
    domain = _reg_domain(urlparse(item["url"]).hostname or "")
    if not domain:
        return {"status": "no_domain"}
    config.load_dotenv()
    mail = imaplib.IMAP4_SSL(IMAP_HOST, 993)
    try:
        mail.login(config.env("GMAIL_ADDRESS"), config.env("GMAIL_APP_PASSWORD"))
        mail.select("INBOX", readonly=True)
        since = (date.today() - timedelta(days=days)).strftime("%d-%b-%Y")
        _, data = mail.search(None, f'(SINCE "{since}" TEXT "{domain}")')
        ids = data[0].split()[-5:]
        for mid in reversed(ids):
            _, msg_data = mail.fetch(mid, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            links = extract_links(_message_text(msg), domain)
            if not links:
                continue
            link = links[0]
            status = _fetch_status(link)
            return {
                "status": "link_opened",
                "link": link,
                "http_status": status,
                "from": msg.get("From", ""),
                "subject": msg.get("Subject", ""),
            }
        return {"status": "no_mail", "domain": domain}
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def cli_verify(item_id: str) -> dict | None:
    state = ledger.load_state()
    item = state["items_cache"].get(item_id)
    if not item:
        import json

        if config.SCORED_PATH.exists():
            scored = json.loads(config.SCORED_PATH.read_text(encoding="utf-8")).get("items", [])
            item = next((i for i in scored if i["id"] == item_id), None)
    if not item:
        log(STAGE, f"找不到項目 item not found: {item_id}")
        return None
    outcome = verify_item(item)
    if outcome is None:
        return None
    log(STAGE, f"verify {item_id}: {outcome['status']}")
    # Do not persist mailbox headers or the raw confirmation URL. Those can
    # carry the operator's address or one-time tokens.
    ledger.append_ledger(
        "verify_done",
        item_id=item_id,
        status=outcome.get("status"),
        http_status=outcome.get("http_status"),
        domain=outcome.get("domain"),
    )
    if outcome.get("status") == "link_opened":
        ledger.append_pack_log(
            item_id,
            f"- {utc_now_iso()} — 驗證信連結已開啟 verify link opened "
            f"(HTTP {outcome.get('http_status')})",
        )
    print({k: v for k, v in outcome.items() if k not in {"link", "from", "subject"}})
    return outcome
