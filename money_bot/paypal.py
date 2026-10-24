"""PayPal Sandbox invoice after an explicit human approval.

Uses the official Invoicing REST API (stdlib urllib only):

  POST /v1/oauth2/token
  POST /v2/invoicing/invoices
  POST /v2/invoicing/invoices/{id}/send   (notifications off)
  GET  /v2/invoicing/invoices/{id}

The send call only moves the draft to a payable status so a link exists.
send_to_recipient and send_to_invoicer stay false, so PayPal is not asked
to email anyone. This module never calls Orders, Payments, Capture, or
Payouts, and it never records a payment.

Sandbox is the default. PAYPAL_MODE=live is honored and discouraged.
When the Client ID or Secret is missing or still "replace-me", every
public function returns a skip result and does not open a socket.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, ROUND_HALF_UP

from . import config
from .util import extract_amounts_usd, log

STAGE = "秋"

SANDBOX_API = "https://api-m.sandbox.paypal.com"
LIVE_API = "https://api-m.paypal.com"

# Used when the scored item has no parsed prize (prize_usd missing and no
# USD-like amount in the title or summary). This is the invoice line amount
# only. The bot never collects it. Override with PAYPAL_PLACEHOLDER_AMOUNT.
PLACEHOLDER_AMOUNT_USD = "1.00"
DEFAULT_RECIPIENT_EMAIL = "sandbox-buyer@example.com"
_PLACEHOLDER_SENTINEL = "replace-me"
_ABSENT = {"", _PLACEHOLDER_SENTINEL}

# Paths this module is allowed to call. Tests assert nothing else is requested.
_ALLOWED_PATHS = (
    "/v1/oauth2/token",
    "/v2/invoicing/invoices",
)


def _present(value: str) -> str:
    text = (value or "").strip()
    if text.lower() in _ABSENT:
        return ""
    return text


def mode() -> str:
    """Return 'live' only when PAYPAL_MODE is exactly live. Anything else is sandbox."""
    config.load_dotenv()
    raw = (config.env("PAYPAL_MODE") or "sandbox").lower()
    if raw == "live":
        return "live"
    return "sandbox"


def api_base(selected: str | None = None) -> str:
    return LIVE_API if (selected or mode()) == "live" else SANDBOX_API


def credentials() -> tuple[str, str, str] | None:
    """(client_id, secret, mode) or None when PayPal should be skipped."""
    config.load_dotenv()
    client_id = _present(config.env("PAYPAL_CLIENT_ID"))
    secret = _present(config.env("PAYPAL_CLIENT_SECRET"))
    if not client_id or not secret:
        return None
    return client_id, secret, mode()


def skip_reason() -> str:
    return (
        "PayPal skipped: set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET "
        "(PayPal Developer Sandbox app). No invoice was created."
    )


def _money(value: float) -> str:
    quantized = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if quantized < Decimal("0.01") or quantized > Decimal("100000000"):
        return ""
    return f"{quantized:.2f}"


def invoice_amount(item: dict) -> tuple[str, str]:
    """Return (usd_value, source) where source is 'parsed' or 'placeholder'."""
    prize = item.get("prize_usd")
    if isinstance(prize, str):
        try:
            prize = float(prize.replace(",", "").replace("$", ""))
        except ValueError:
            prize = None
    if isinstance(prize, (int, float)) and not isinstance(prize, bool):
        formatted = _money(float(prize))
        if formatted:
            return formatted, "parsed"
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    amounts = extract_amounts_usd(text)
    if amounts:
        formatted = _money(max(amounts))
        if formatted:
            return formatted, "parsed"
    override = _present(config.env("PAYPAL_PLACEHOLDER_AMOUNT")) or PLACEHOLDER_AMOUNT_USD
    try:
        formatted = _money(float(override))
    except ValueError:
        formatted = ""
    return formatted or PLACEHOLDER_AMOUNT_USD, "placeholder"


def _clean(text: str, limit: int, fallback: str) -> str:
    cleaned = " ".join((text or "").split())
    cleaned = "".join(ch for ch in cleaned if ch.isprintable())
    return (cleaned or fallback)[:limit]


def recipient_email() -> str:
    return _present(config.env("PAYPAL_RECIPIENT_EMAIL")) or DEFAULT_RECIPIENT_EMAIL


def build_invoice_body(item: dict) -> dict:
    """JSON body for POST /v2/invoicing/invoices. No credentials, no payment block."""
    amount, _source = invoice_amount(item)
    title = _clean(item.get("title", ""), 200, "Approved opportunity")
    url = _clean(item.get("url", ""), 500, "")
    item_id = _clean(str(item.get("id", "")), 40, "item")
    description = _clean(
        "Approved opportunity "
        f"{item_id}. Sandbox demo invoice only; this bot does not pay it. {url}",
        1000,
        "Sandbox demo invoice. This bot does not pay it.",
    )
    number = ("RMA" + item_id)[:25]
    return {
        "detail": {
            "invoice_number": number,
            "currency_code": "USD",
            "reference": item_id[:120],
            "note": (
                "Created by rensheng-money-agent after explicit human approval. "
                "Sandbox demo of a money trail. This bot never pays this invoice."
            ),
            "payment_term": {"term_type": "NO_DUE_DATE"},
        },
        "invoicer": {"business_name": "Rensheng Money Agent"},
        "primary_recipients": [
            {"billing_info": {"email_address": recipient_email()}}
        ],
        "items": [
            {
                "name": title,
                "description": description,
                "quantity": "1",
                "unit_amount": {"currency_code": "USD", "value": amount},
            }
        ],
    }


def public_fields(result: dict) -> dict:
    """Approval-state slice. Never includes tokens or client credentials."""
    keys = (
        "skipped",
        "reason",
        "error",
        "invoice_id",
        "status",
        "link",
        "merchant_link",
        "amount",
        "currency",
        "amount_source",
        "mode",
    )
    out: dict = {}
    for key in keys:
        value = result.get(key)
        if key == "skipped":
            if value:
                out[key] = True
            continue
        if value not in (None, ""):
            out[key] = value
    return out


def dry_run(item: dict | None = None) -> dict:
    """Describe the invoice that approve() would create. Never contacts PayPal."""
    sample = item or {
        "id": "dryrun",
        "title": "Sandbox dry run",
        "url": "https://example.com/dry-run",
        "summary": "No network call.",
        "prize_usd": None,
    }
    amount, source = invoice_amount(sample)
    selected = mode()
    present = credentials() is not None
    note = (
        "Dry run only. No request was sent. "
        + (
            "Credentials are set; `approve` will create a sandbox invoice and will not pay it."
            if present and selected == "sandbox"
            else "PAYPAL_MODE=live. `approve` would create a real invoice and still would not pay it. Live mode is discouraged."
            if present
            else "PayPal skipped until PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET are set. No invoice was created."
        )
    )
    return {
        "dry_run": True,
        "credentials_present": present,
        "mode": selected,
        "would_post": f"{api_base(selected)}/v2/invoicing/invoices",
        "amount": amount,
        "amount_source": source,
        "currency": "USD",
        "placeholder_amount_usd": PLACEHOLDER_AMOUNT_USD,
        "body": build_invoice_body(sample),
        "note": note,
    }


def _urllib_transport(method: str, url: str, headers: dict, body: bytes | None):
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=config.HTTP_TIMEOUT) as response:
            raw = response.read()
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
            return response.status, parsed, {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            parsed = {"message": raw.decode("utf-8", "replace")[:300]}
        return exc.code, parsed if isinstance(parsed, dict) else {"message": str(parsed)[:300]}, {}
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc


def _error_text(status: int, body) -> str:
    if not isinstance(body, dict):
        return f"HTTP {status}"
    parts = [
        str(body.get("name") or body.get("error") or ""),
        str(body.get("message") or body.get("error_description") or ""),
    ]
    details = body.get("details")
    if isinstance(details, list) and details and isinstance(details[0], dict):
        parts.append(str(details[0].get("description") or details[0].get("issue") or ""))
    text = " ".join(part for part in parts if part and part != "None").strip()
    return f"HTTP {status} {text}".strip()[:400]


def _invoice_id(body: dict, headers: dict) -> str:
    if isinstance(body, dict) and body.get("id"):
        return str(body["id"])
    href = ""
    if isinstance(body, dict):
        href = str(body.get("href") or "")
        if not href:
            for link in body.get("links") or []:
                if isinstance(link, dict) and link.get("rel") == "self":
                    href = str(link.get("href") or "")
                    break
    if not href:
        href = str(headers.get("location") or "")
    if "/invoices/" in href:
        return href.rstrip("/").split("/")[-1]
    return ""


def _links(invoice: dict) -> tuple[str, str]:
    """Return (payer_or_best_link, merchant_link)."""
    if not isinstance(invoice, dict):
        return "", ""
    detail = invoice.get("detail") if isinstance(invoice.get("detail"), dict) else {}
    meta = detail.get("metadata") if isinstance(detail.get("metadata"), dict) else {}
    recipient = str(meta.get("recipient_view_url") or "")
    merchant = str(meta.get("invoicer_view_url") or "")
    payer = ""
    self_href = ""
    for link in invoice.get("links") or []:
        if not isinstance(link, dict):
            continue
        rel = str(link.get("rel") or "")
        href = str(link.get("href") or "")
        if rel in {"payer-view", "payer_view"} and href:
            payer = href
        elif rel == "self" and href:
            self_href = href
    best = payer or recipient or merchant or self_href
    return best, merchant


def _merchant_fallback(invoice_id: str, selected: str) -> str:
    if not invoice_id:
        return ""
    host = "www.paypal.com" if selected == "live" else "www.sandbox.paypal.com"
    safe_id = urllib.parse.quote(invoice_id, safe="-")
    return f"https://{host}/invoice/details/{safe_id}"


def _assert_allowed(url: str) -> None:
    """Only OAuth, create, get, and silent-send. Never orders, capture, or payouts."""
    path = urllib.parse.urlparse(url).path.rstrip("/") or "/"
    if path in _ALLOWED_PATHS:
        return
    prefix = "/v2/invoicing/invoices/"
    if path.startswith(prefix):
        parts = path[len(prefix):].split("/")
        if len(parts) == 1 and parts[0]:
            return
        if len(parts) == 2 and parts[0] and parts[1] == "send":
            return
    raise RuntimeError(f"refusing non-invoice PayPal path: {path}")


def create_invoice_for_item(item: dict, transport=None) -> dict:
    """Create one invoice for an already-approved item. Never pays it.

    `transport` is a test hook `(method, url, headers, body_bytes) ->
    (status, json_body, headers)`. The default talks to PayPal over urllib.
    """
    creds = credentials()
    selected = mode()
    amount, source = invoice_amount(item)
    if creds is None:
        return {
            "skipped": True,
            "reason": skip_reason(),
            "amount": amount,
            "currency": "USD",
            "amount_source": source,
            "mode": selected,
        }
    if selected == "live":
        log(
            STAGE,
            "PAYPAL_MODE=live — creating a REAL PayPal invoice. Live mode is discouraged. The bot will not pay it.",
        )

    client_id, secret, _mode = creds
    call = transport or _urllib_transport
    base = api_base(selected)
    try:
        token = _access_token(call, base, client_id, secret)
        created = _post_invoice(call, base, token, item)
        invoice_id = created["invoice_id"]
        invoice = created["invoice"]
        status = str(invoice.get("status") or "DRAFT")
        link, merchant = _links(invoice)
        send_error = _send_silent(call, base, token, invoice_id)
        if not send_error:
            fetched = _get_invoice(call, base, token, invoice_id)
            if fetched:
                invoice = fetched
                status = str(invoice.get("status") or status)
                fetched_link, fetched_merchant = _links(invoice)
                link = fetched_link or link
                merchant = fetched_merchant or merchant
        if not merchant:
            merchant = _merchant_fallback(invoice_id, selected)
        if not link:
            link = merchant
        result = {
            "invoice_id": invoice_id,
            "status": status,
            "link": link,
            "merchant_link": merchant,
            "amount": amount,
            "currency": "USD",
            "amount_source": source,
            "mode": selected,
        }
        if send_error:
            result["send_note"] = send_error
        return result
    except Exception as exc:  # approval must survive a PayPal outage
        log(STAGE, f"PayPal invoice failed (no payment was made): {exc}")
        return {
            "error": f"PayPal invoice failed: {exc}",
            "amount": amount,
            "currency": "USD",
            "amount_source": source,
            "mode": selected,
        }


def _access_token(call, base: str, client_id: str, secret: str) -> str:
    url = f"{base}/v1/oauth2/token"
    _assert_allowed(url)
    basic = base64.b64encode(f"{client_id}:{secret}".encode("utf-8")).decode("ascii")
    status, body, _headers = call(
        "POST",
        url,
        {
            "Authorization": f"Basic {basic}",
            "Accept": "application/json",
            "Accept-Language": "en_US",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": config.USER_AGENT,
        },
        urllib.parse.urlencode({"grant_type": "client_credentials"}).encode("utf-8"),
    )
    if status >= 400 or not isinstance(body, dict) or not body.get("access_token"):
        raise RuntimeError(f"PayPal auth failed: {_error_text(status, body)}")
    return str(body["access_token"])


def _post_invoice(call, base: str, token: str, item: dict) -> dict:
    url = f"{base}/v2/invoicing/invoices"
    _assert_allowed(url)
    item_id = _clean(str(item.get("id", "item")), 40, "item")
    status, body, headers = call(
        "POST",
        url,
        {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
            "PayPal-Request-Id": f"rma-invoice-{item_id}"[:100],
            "User-Agent": config.USER_AGENT,
        },
        json.dumps(build_invoice_body(item)).encode("utf-8"),
    )
    if status >= 400 or not isinstance(body, dict):
        raise RuntimeError(f"PayPal create invoice failed: {_error_text(status, body)}")
    invoice_id = _invoice_id(body, {k.lower(): v for k, v in (headers or {}).items()})
    if not invoice_id:
        raise RuntimeError("PayPal create invoice failed: response had no invoice id")
    return {"invoice_id": invoice_id, "invoice": body}


def _send_silent(call, base: str, token: str, invoice_id: str) -> str:
    """Move the draft to payable without emailing. Empty string means success."""
    safe_id = urllib.parse.quote(invoice_id, safe="-")
    url = f"{base}/v2/invoicing/invoices/{safe_id}/send"
    _assert_allowed(url)
    try:
        status, body, _headers = call(
            "POST",
            url,
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": config.USER_AGENT,
            },
            json.dumps(
                {"send_to_recipient": False, "send_to_invoicer": False}
            ).encode("utf-8"),
        )
    except Exception as exc:
        return f"send skipped: {exc}"
    if status >= 400:
        return f"draft created; send did not complete ({_error_text(status, body)})"
    return ""


def _get_invoice(call, base: str, token: str, invoice_id: str) -> dict:
    safe_id = urllib.parse.quote(invoice_id, safe="-")
    url = f"{base}/v2/invoicing/invoices/{safe_id}"
    _assert_allowed(url)
    try:
        status, body, _headers = call(
            "GET",
            url,
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": config.USER_AGENT,
            },
            None,
        )
    except Exception:
        return {}
    if status >= 400 or not isinstance(body, dict):
        return {}
    return body
