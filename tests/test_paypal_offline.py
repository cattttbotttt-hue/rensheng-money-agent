"""Offline PayPal invoice tests. No network. No real credentials.

Run from the repo root:  python3 -m unittest tests.test_paypal_offline -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from money_bot import config, executor, ledger, paypal  # noqa: E402

PAYPAL_KEYS = (
    "PAYPAL_CLIENT_ID",
    "PAYPAL_CLIENT_SECRET",
    "PAYPAL_MODE",
    "PAYPAL_RECIPIENT_EMAIL",
    "PAYPAL_INVOICER_EMAIL",
    "PAYPAL_PLACEHOLDER_AMOUNT",
    "MONEY_BOT_PUBLISH",
    # Approve now asks for advice. Keep that path offline in this file.
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
)
PATH_ATTRS = (
    "DATA_DIR",
    "RAW_PATH",
    "SCORED_PATH",
    "STATE_PATH",
    "LEDGER_PATH",
    "VAULT_ROOT",
    "VAULT_NOTES_DIR",
    "VAULT_PACKS_DIR",
    "PACKS_DIR",
)
EXAMPLE = Path(__file__).resolve().parents[1] / "data" / "examples" / "scored_item.example.json"


def _item(**overrides) -> dict:
    base = {
        "id": "example000001",
        "title": "Example worldwide giveaway",
        "url": "https://example.com/giveaway",
        "summary": "Open worldwide. Email only.",
        "prize_usd": 50,
        "category": "giveaway",
        "source": "Example feed",
    }
    base.update(overrides)
    return base


class _EnvGuard(unittest.TestCase):
    def setUp(self):
        self._saved_env = {key: os.environ.get(key) for key in PAYPAL_KEYS}
        for key in PAYPAL_KEYS:
            os.environ.pop(key, None)
        self._load_dotenv = config.load_dotenv
        config.load_dotenv = lambda: None

    def tearDown(self):
        config.load_dotenv = self._load_dotenv
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class PaypalSkipAndAmountTests(_EnvGuard):
    def test_env_example_has_replace_me_placeholders(self):
        text = (Path(__file__).resolve().parents[1] / ".env.example").read_text(encoding="utf-8")
        self.assertIn("PAYPAL_CLIENT_ID=replace-me", text)
        self.assertIn("PAYPAL_CLIENT_SECRET=replace-me", text)
        self.assertIn("PAYPAL_MODE=sandbox", text)
        self.assertIn("PAYPAL_RECIPIENT_EMAIL=", text)
        self.assertIn("PAYPAL_INVOICER_EMAIL=", text)
        self.assertNotIn("PAYPAL_CLIENT_SECRET=A", text)
        self.assertNotRegex(text, r"sb-[A-Za-z0-9]+@business\.example\.com")

    def test_missing_and_placeholder_credentials_skip_without_network(self):
        calls = []

        def transport(*_args, **_kwargs):
            calls.append(1)
            raise AssertionError("network")

        missing = paypal.create_invoice_for_item(_item(), transport=transport)
        self.assertTrue(missing["skipped"])
        self.assertIn("PayPal skipped", missing["reason"])
        self.assertNotIn("invoice_id", missing)

        os.environ["PAYPAL_CLIENT_ID"] = "replace-me"
        os.environ["PAYPAL_CLIENT_SECRET"] = "replace-me"
        placeholder = paypal.create_invoice_for_item(_item(), transport=transport)
        self.assertTrue(placeholder["skipped"])
        self.assertEqual(calls, [])

    def test_parsed_prize_and_placeholder_amount(self):
        amount, source = paypal.invoice_amount(_item(prize_usd=50))
        self.assertEqual((amount, source), ("50.00", "parsed"))
        amount, source = paypal.invoice_amount(
            _item(prize_usd=None, title="Win $250 cash", summary="")
        )
        self.assertEqual((amount, source), ("250.00", "parsed"))
        amount, source = paypal.invoice_amount(
            _item(prize_usd=None, title="No figure here", summary="email only")
        )
        self.assertEqual((amount, source), (paypal.PLACEHOLDER_AMOUNT_USD, "placeholder"))
        self.assertEqual(paypal.PLACEHOLDER_AMOUNT_USD, "1.00")

    def test_dry_run_prints_body_and_skip_note(self):
        report = paypal.dry_run(_item(prize_usd=None, title="No figure here", summary=""))
        self.assertTrue(report["dry_run"])
        self.assertFalse(report["credentials_present"])
        self.assertEqual(report["mode"], "sandbox")
        self.assertIn("api-m.sandbox.paypal.com", report["would_post"])
        self.assertEqual(report["amount"], "1.00")
        self.assertEqual(report["amount_source"], "placeholder")
        self.assertIn("skipped", report["note"].lower())
        self.assertEqual(report["body"]["items"][0]["name"], "No figure here")
        blob = json.dumps(report)
        self.assertNotIn("authorization", blob.lower())
        self.assertNotIn("sandbox-secret", blob.lower())
        for banned in ("/v1/payments", "/v2/checkout", "payouts", "capture"):
            self.assertNotIn(banned, blob)

    def test_refuses_payment_paths(self):
        with self.assertRaises(RuntimeError):
            paypal._assert_allowed("https://api-m.sandbox.paypal.com/v1/payments/payouts")
        with self.assertRaises(RuntimeError):
            paypal._assert_allowed("https://api-m.sandbox.paypal.com/v2/checkout/orders")
        with self.assertRaises(RuntimeError):
            paypal._assert_allowed(
                "https://api-m.sandbox.paypal.com/v2/invoicing/invoices/INV2-X/payments"
            )


class _FakePayPal:
    def __init__(
        self,
        *,
        send_status: int = 200,
        token_status: int = 200,
        scope: str | None = paypal.INVOICING_SCOPE,
    ):
        self.send_status = send_status
        self.token_status = token_status
        self.scope = scope
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "body": body.decode("utf-8") if isinstance(body, bytes) else body,
            }
        )
        if url.endswith("/v1/oauth2/token"):
            if self.token_status >= 400:
                return self.token_status, {"error": "invalid_client", "error_description": "nope"}, {}
            token = {"access_token": "test-token", "token_type": "Bearer"}
            if self.scope is not None:
                token["scope"] = self.scope
            return 200, token, {}
        if url.endswith("/v2/invoicing/invoices") and method == "POST":
            return 201, {
                "id": "INV2-TEST-0001",
                "status": "DRAFT",
                "detail": {
                    "metadata": {
                        "recipient_view_url": "https://www.sandbox.paypal.com/invoice/p/#TEST",
                        "invoicer_view_url": "https://www.sandbox.paypal.com/invoice/details/INV2-TEST-0001",
                    }
                },
                "links": [
                    {
                        "rel": "self",
                        "href": "https://api-m.sandbox.paypal.com/v2/invoicing/invoices/INV2-TEST-0001",
                    }
                ],
            }, {}
        if url.endswith("/send"):
            if self.send_status >= 400:
                return self.send_status, {"name": "UNPROCESSABLE_ENTITY", "message": "nope"}, {}
            return 200, {}, {}
        if method == "GET":
            return 200, {
                "id": "INV2-TEST-0001",
                "status": "SENT",
                "detail": {
                    "metadata": {
                        "recipient_view_url": "https://www.sandbox.paypal.com/invoice/p/#TEST",
                        "invoicer_view_url": "https://www.sandbox.paypal.com/invoice/details/INV2-TEST-0001",
                    }
                },
                "links": [
                    {"rel": "payer-view", "href": "https://www.sandbox.paypal.com/invoice/p/#TEST"}
                ],
            }, {}
        raise AssertionError(url)


class PaypalCreateTests(_EnvGuard):
    def setUp(self):
        super().setUp()
        os.environ["PAYPAL_CLIENT_ID"] = "sandbox-client-id"
        os.environ["PAYPAL_CLIENT_SECRET"] = "sandbox-secret-value"
        os.environ["PAYPAL_MODE"] = "sandbox"

    def test_create_uses_title_parsed_amount_and_invoice_api_only(self):
        transport = _FakePayPal()
        result = paypal.create_invoice_for_item(_item(), transport=transport)
        self.assertEqual(result["invoice_id"], "INV2-TEST-0001")
        self.assertEqual(result["status"], "SENT")
        self.assertEqual(result["link"], "https://www.sandbox.paypal.com/invoice/p/#TEST")
        self.assertEqual(result["amount"], "50.00")
        self.assertEqual(result["amount_source"], "parsed")
        self.assertEqual(result["mode"], "sandbox")
        self.assertNotIn("error", result)

        urls = [call["url"] for call in transport.calls]
        self.assertTrue(urls[0].startswith(paypal.SANDBOX_API + "/v1/oauth2/token"))
        self.assertEqual(urls[1], paypal.SANDBOX_API + "/v2/invoicing/invoices")
        self.assertTrue(urls[2].endswith("/v2/invoicing/invoices/INV2-TEST-0001/send"))
        self.assertTrue(urls[3].endswith("/v2/invoicing/invoices/INV2-TEST-0001"))
        for url in urls:
            for banned in ("payout", "checkout", "capture", "/v1/payments", "/v2/payments"):
                self.assertNotIn(banned, url)

        invoice = json.loads(transport.calls[1]["body"])
        self.assertEqual(invoice["items"][0]["name"], "Example worldwide giveaway")
        self.assertEqual(invoice["items"][0]["unit_amount"]["value"], "50.00")
        self.assertNotIn("payments", invoice)
        self.assertNotIn("sandbox-secret-value", transport.calls[1]["body"])
        send_body = json.loads(transport.calls[2]["body"])
        self.assertFalse(send_body["send_to_recipient"])
        self.assertFalse(send_body["send_to_invoicer"])
        self.assertTrue(transport.calls[0]["headers"]["Authorization"].startswith("Basic "))
        self.assertNotIn("sandbox-secret-value", transport.calls[0]["url"])
        form = urllib.parse.parse_qs(transport.calls[0]["body"])
        self.assertEqual(form.get("grant_type"), ["client_credentials"])
        self.assertEqual(form.get("scope"), [paypal.INVOICING_SCOPE])
        self.assertNotIn("email_address", invoice["invoicer"])

    def test_missing_invoicing_scope_stops_before_create(self):
        scopes = (
            "https://uri.paypal.com/services/subscriptions",
            "",
            None,
        )
        for scope in scopes:
            transport = _FakePayPal(scope=scope)
            result = paypal.create_invoice_for_item(_item(), transport=transport)
            self.assertNotIn("invoice_id", result, scope)
            self.assertIn("missing the invoicing scope", result["error"])
            self.assertIn(paypal.INVOICING_SCOPE, result["error"])
            self.assertIn("Developer Dashboard → Sandbox", result["error"])
            self.assertIn("new Sandbox app with Invoicing checked", result["error"])
            self.assertNotIn("NOT_AUTHORIZED", result["error"])
            self.assertNotIn("sandbox-secret-value", result["error"])
            self.assertNotIn("test-token", result["error"])
            self.assertEqual(len(transport.calls), 1)
            self.assertTrue(transport.calls[0]["url"].endswith("/v1/oauth2/token"))

    def test_readwrite_invoicing_scope_is_enough(self):
        transport = _FakePayPal(
            scope="https://uri.paypal.com/services/invoicing/invoices/readwrite"
        )
        result = paypal.create_invoice_for_item(_item(), transport=transport)
        self.assertEqual(result["invoice_id"], "INV2-TEST-0001")
        self.assertNotIn("error", result)

    def test_invalid_scope_on_token_includes_fix_hint(self):
        calls = []

        def transport(_method, url, _headers, _body):
            calls.append(url)
            return 400, {
                "error": "invalid_scope",
                "error_description": "Invalid scope requested",
            }, {}

        result = paypal.create_invoice_for_item(_item(), transport=transport)
        self.assertNotIn("invoice_id", result)
        self.assertIn("invalid_scope", result["error"])
        self.assertIn("Developer Dashboard → Sandbox", result["error"])
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].endswith("/v1/oauth2/token"))

    def test_invoicer_email_is_optional_on_the_invoice_body(self):
        omitted = paypal.build_invoice_body(_item())
        self.assertEqual(omitted["invoicer"], {"business_name": "Rensheng Money Agent"})

        os.environ["PAYPAL_INVOICER_EMAIL"] = "  sb-merchant@business.example.com  "
        transport = _FakePayPal()
        result = paypal.create_invoice_for_item(_item(), transport=transport)
        self.assertEqual(result["invoice_id"], "INV2-TEST-0001")
        invoice = json.loads(transport.calls[1]["body"])
        self.assertEqual(invoice["invoicer"]["business_name"], "Rensheng Money Agent")
        self.assertEqual(
            invoice["invoicer"]["email_address"],
            "sb-merchant@business.example.com",
        )
        self.assertNotIn("sandbox-secret-value", transport.calls[1]["body"])

        os.environ["PAYPAL_INVOICER_EMAIL"] = "replace-me"
        placeholder = paypal.build_invoice_body(_item())
        self.assertNotIn("email_address", placeholder["invoicer"])

    def test_live_mode_is_explicit_and_unknown_mode_stays_sandbox(self):
        os.environ["PAYPAL_MODE"] = "live"
        live = _FakePayPal()
        result = paypal.create_invoice_for_item(_item(), transport=live)
        self.assertEqual(result["mode"], "live")
        self.assertTrue(live.calls[0]["url"].startswith(paypal.LIVE_API))

        os.environ["PAYPAL_MODE"] = "production"
        sandbox = _FakePayPal()
        result = paypal.create_invoice_for_item(_item(), transport=sandbox)
        self.assertEqual(result["mode"], "sandbox")
        self.assertTrue(sandbox.calls[0]["url"].startswith(paypal.SANDBOX_API))

    def test_auth_and_send_failures_do_not_raise(self):
        denied = _FakePayPal(token_status=401)
        failed = paypal.create_invoice_for_item(_item(), transport=denied)
        self.assertIn("error", failed)
        self.assertNotIn("invoice_id", failed)
        self.assertNotIn("sandbox-secret-value", failed["error"])

        draft_only = _FakePayPal(send_status=422)
        created = paypal.create_invoice_for_item(_item(), transport=draft_only)
        self.assertEqual(created["invoice_id"], "INV2-TEST-0001")
        self.assertEqual(created["status"], "DRAFT")
        self.assertIn("send", created["send_note"])

        def explode(*_args, **_kwargs):
            raise TimeoutError("timed out")

        down = paypal.create_invoice_for_item(_item(), transport=explode)
        self.assertIn("timed out", down["error"])
        self.assertNotIn("invoice_id", down)


class ApproveLedgerTests(_EnvGuard):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._saved_paths = {name: getattr(config, name) for name in PATH_ATTRS}
        config.DATA_DIR = root / "data"
        config.RAW_PATH = config.DATA_DIR / "raw_items.json"
        config.SCORED_PATH = config.DATA_DIR / "scored_items.json"
        config.STATE_PATH = config.DATA_DIR / "state.json"
        config.LEDGER_PATH = config.DATA_DIR / "ledger.jsonl"
        config.VAULT_ROOT = root
        config.VAULT_NOTES_DIR = root / "notes"
        config.VAULT_PACKS_DIR = root / "notes" / "packs"
        config.PACKS_DIR = config.VAULT_PACKS_DIR
        config.ensure_data_dirs()
        self.item = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        config.SCORED_PATH.write_text(
            json.dumps({"count": 1, "items": [self.item]}),
            encoding="utf-8",
        )
        self._create = paypal.create_invoice_for_item

    def tearDown(self):
        paypal.create_invoice_for_item = self._create
        for name, value in self._saved_paths.items():
            setattr(config, name, value)
        self.tmp.cleanup()
        super().tearDown()

    def _events(self) -> list[dict]:
        text = config.LEDGER_PATH.read_text(encoding="utf-8")
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def _pack(self) -> str:
        return (config.VAULT_PACKS_DIR / f"{self.item['id']}.md").read_text(encoding="utf-8")

    def test_approve_without_credentials_skips_and_still_writes_pack(self):
        approved = executor.approve(self.item["id"])
        self.assertEqual(approved["id"], self.item["id"])
        pack = self._pack()
        self.assertIn("PayPal skipped", pack)
        self.assertIn("No invoice was created", pack)
        events = self._events()
        self.assertIn("item_approved", [event["event"] for event in events])
        skipped = [event for event in events if event["event"] == "paypal_invoice_skipped"]
        self.assertEqual(len(skipped), 1)
        self.assertIn("PayPal skipped", skipped[0]["reason"])
        state = json.loads(config.STATE_PATH.read_text(encoding="utf-8"))
        self.assertTrue(state["approvals"][self.item["id"]]["paypal"]["skipped"])

    def test_approve_records_invoice_once_then_retry_after_skip(self):
        executor.approve(self.item["id"])
        calls = []

        def fake(item):
            calls.append(item["id"])
            return {
                "invoice_id": "INV2-DEMO-1",
                "status": "SENT",
                "link": "https://www.sandbox.paypal.com/invoice/p/#DEMO",
                "merchant_link": "https://www.sandbox.paypal.com/invoice/details/INV2-DEMO-1",
                "amount": "50.00",
                "currency": "USD",
                "amount_source": "parsed",
                "mode": "sandbox",
            }

        paypal.create_invoice_for_item = fake
        again = executor.approve(self.item["id"])
        self.assertEqual(again["id"], self.item["id"])
        self.assertEqual(calls, [self.item["id"]])
        pack = self._pack()
        self.assertIn("INV2-DEMO-1", pack)
        self.assertIn("https://www.sandbox.paypal.com/invoice/p/#DEMO", pack)
        self.assertIn("SENT", pack)
        self.assertNotIn("No invoice was created", pack)
        created = [event for event in self._events() if event["event"] == "paypal_invoice_created"]
        self.assertEqual(created[0]["invoice_id"], "INV2-DEMO-1")
        self.assertEqual(created[0]["status"], "SENT")
        self.assertEqual(created[0]["link"], "https://www.sandbox.paypal.com/invoice/p/#DEMO")
        third = executor.approve(self.item["id"])
        self.assertIsNone(third)
        self.assertEqual(calls, [self.item["id"]])

    def test_telegram_poll_keeps_invoice_on_the_approval(self):
        os.environ["PAYPAL_CLIENT_ID"] = "sandbox-client-id"
        os.environ["PAYPAL_CLIENT_SECRET"] = "sandbox-secret-value"

        def fake(_item):
            return {
                "invoice_id": "INV2-TG-1",
                "status": "SENT",
                "link": "https://www.sandbox.paypal.com/invoice/p/#TG",
                "amount": "50.00",
                "currency": "USD",
                "amount_source": "parsed",
                "mode": "sandbox",
            }

        paypal.create_invoice_for_item = fake

        class FakeTrio:
            def __init__(self):
                self.enabled = True
                self.autumn = type("Autumn", (), {"enabled": True})()
                self.sent = []

            def get_updates(self, offset=0):
                return [
                    {
                        "update_id": 7,
                        "callback_query": {
                            "id": "cb1",
                            "data": f"appr:{self_item}",
                            "from": {"username": "ada"},
                        },
                    }
                ]

            def answer_callback(self, _cid, _text):
                return None

            def send(self, _season, text):
                self.sent.append(text)

        self_item = self.item["id"]
        original = executor.TelegramTrio
        executor.TelegramTrio = FakeTrio
        try:
            handled = executor.poll()
        finally:
            executor.TelegramTrio = original
        self.assertEqual(handled, 1)
        state = json.loads(config.STATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(state["telegram_update_offset"], 8)
        self.assertEqual(state["approvals"][self.item["id"]]["paypal"]["invoice_id"], "INV2-TG-1")
        self.assertIn("INV2-TG-1", self._pack())
        self.assertIn("paypal_invoice_created", [event["event"] for event in self._events()])


if __name__ == "__main__":
    unittest.main()
