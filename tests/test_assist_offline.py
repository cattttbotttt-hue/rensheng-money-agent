"""Offline AI assist tests. No network. No live model call.

Run from the repo root:  python3 -m unittest tests.test_assist_offline -v
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.request
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from money_bot import assist, config, executor, ledger, paypal  # noqa: E402

ENV_KEYS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "PAYPAL_CLIENT_ID",
    "PAYPAL_CLIENT_SECRET",
    "PAYPAL_MODE",
    "PAYPAL_RECIPIENT_EMAIL",
    "PAYPAL_PLACEHOLDER_AMOUNT",
    "MONEY_BOT_PUBLISH",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_TOKEN_SPRING",
    "TELEGRAM_TOKEN_SUMMER",
    "TELEGRAM_TOKEN_AUTUMN",
    "TELEGRAM_CHAT_ID",
    "PROFILE_NAME",
    "PROFILE_EMAIL",
    "PROFILE_PHONE",
    "PROFILE_ADDRESS",
    "PROFILE_WALLET_EVM",
    "PROFILE_TWITTER",
    "PROFILE_TELEGRAM",
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
ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "data" / "examples" / "scored_item.example.json"


def _item(**overrides) -> dict:
    base = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    base.update(overrides)
    return base


class _OfflineGuard(unittest.TestCase):
    def setUp(self):
        self._saved_env = {key: os.environ.get(key) for key in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        self._load_dotenv = config.load_dotenv
        config.load_dotenv = lambda: None
        self._urlopen = urllib.request.urlopen

        def explode(*_args, **_kwargs):
            raise AssertionError("live network is not allowed in offline AI tests")

        urllib.request.urlopen = explode

    def tearDown(self):
        urllib.request.urlopen = self._urlopen
        config.load_dotenv = self._load_dotenv
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class PromptParseAndHeuristicTests(_OfflineGuard):
    def test_env_example_and_readme_document_both_paths(self):
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("OPENAI_API_KEY=replace-me", example)
        self.assertIn("OPENAI_BASE_URL=https://api.openai.com/v1", example)
        self.assertIn("OPENAI_MODEL=gpt-4o-mini", example)
        self.assertNotIn("sk-", example)
        self.assertIn("## AI assist", readme)
        self.assertIn("gpt-4o-mini", readme)
        self.assertIn("AI provider skipped", readme)
        self.assertIn("advice only", readme.lower())
        self.assertIn("urllib", readme)

    def test_missing_and_placeholder_keys_skip_the_provider(self):
        calls = []

        def transport(*_args, **_kwargs):
            calls.append(1)
            raise AssertionError("network")

        missing = assist.advise(_item(), transport=transport)
        self.assertTrue(missing["skipped"])
        self.assertFalse(missing.get("fallback"))
        self.assertEqual(missing["provider"], "offline-heuristic")
        self.assertEqual(missing["recommendation"], "approve")
        self.assertIn("AI provider skipped", missing["summary"])
        self.assertIn("AI provider skipped", missing["reason"])
        self.assertIn("Risk is modest and the effort looks light.", missing["summary"])
        self.assertTrue(missing["advises_only"])

        os.environ["OPENAI_API_KEY"] = "replace-me"
        placeholder = assist.advise(_item(), transport=transport)
        self.assertTrue(placeholder["skipped"])
        self.assertEqual(calls, [])
        self.assertNotIn("replace-me", json.dumps(placeholder))

    def test_heuristic_skip_rules(self):
        risky = assist.heuristic_decision(_item(risk=0.5, effort=0.9, score=80))
        self.assertEqual(risky[0], "skip")
        heavy = assist.heuristic_decision(_item(risk=0.1, effort=0.2, score=80))
        self.assertEqual(heavy[0], "skip")
        low_score = assist.heuristic_decision(_item(risk=0.0, effort=0.8, score=30))
        self.assertEqual(low_score[0], "skip")
        secret = assist.heuristic_decision(
            _item(title="Send your seed phrase", risk=0.0, effort=0.9, score=90)
        )
        self.assertEqual(secret[0], "skip")
        fee = assist.heuristic_decision(
            _item(summary="Pay the processing fee first", risk=0.0, effort=0.9, score=90)
        )
        self.assertEqual(fee[0], "skip")
        easy = assist.heuristic_decision(_item())
        self.assertEqual(easy, ("approve", "Risk is modest and the effort looks light."))

    def test_prompt_has_the_item_and_no_secrets(self):
        os.environ["OPENAI_API_KEY"] = "sk-testsecretvalue"
        os.environ["PROFILE_EMAIL"] = "secret-profile@example.com"
        os.environ["PAYPAL_CLIENT_SECRET"] = "sandbox-secret-value"
        messages = assist.build_messages(_item())
        blob = json.dumps(messages)
        self.assertIn("Example worldwide giveaway", blob)
        self.assertIn("do not approve", blob.lower())
        self.assertIn("advice only", blob.lower())
        self.assertNotIn("sk-testsecretvalue", blob)
        self.assertNotIn("secret-profile@example.com", blob)
        self.assertNotIn("sandbox-secret-value", blob)
        self.assertNotIn("PROFILE_EMAIL", blob)

    def test_parse_fenced_json_and_reject_payment_advice(self):
        parsed = assist.parse_advice(
            '```json\n{"summary": "Light email entry.", "recommendation": "skip", "reason": "Small prize."}\n```'
        )
        self.assertEqual(parsed["recommendation"], "skip")
        self.assertEqual(parsed["summary"], "Light email entry.")
        self.assertIsNone(assist.parse_advice("not json"))
        self.assertIsNone(
            assist.parse_advice('{"summary": "Pay it now.", "recommendation": "pay", "reason": "now"}')
        )
        self.assertIsNone(assist.parse_advice('{"recommendation": "approve"}'))

    def test_model_call_parses_advice_and_stays_on_chat_completions(self):
        os.environ["OPENAI_API_KEY"] = "sk-testsecretvalue"
        os.environ["OPENAI_MODEL"] = "demo-model"
        os.environ["OPENAI_BASE_URL"] = "https://example.test/v1/"
        seen = {}

        def transport(method, url, headers, body):
            seen["method"] = method
            seen["url"] = url
            seen["headers"] = headers
            seen["body"] = json.loads(body.decode("utf-8"))
            return (
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "content": [
                                    {
                                        "text": json.dumps(
                                            {
                                                "summary": "Light email entry, low scam risk.",
                                                "recommendation": "skip",
                                                "reason": "Prize is small relative to the signup.",
                                            }
                                        )
                                    }
                                ]
                            }
                        }
                    ]
                },
                {},
            )

        advice = assist.advise(_item(), transport=transport)
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["url"], "https://example.test/v1/chat/completions")
        self.assertTrue(seen["headers"]["Authorization"].startswith("Bearer "))
        self.assertNotIn("sk-testsecretvalue", seen["body"].__repr__())
        self.assertEqual(seen["body"]["model"], "demo-model")
        self.assertEqual(seen["body"]["messages"][0]["role"], "system")
        self.assertIn("Example worldwide giveaway", json.dumps(seen["body"]["messages"]))
        self.assertFalse(advice["skipped"])
        self.assertFalse(advice.get("fallback"))
        self.assertEqual(advice["provider"], "openai-compatible")
        self.assertEqual(advice["model"], "demo-model")
        self.assertEqual(advice["recommendation"], "skip")
        self.assertIn("Light email entry", advice["summary"])
        self.assertNotIn("sk-testsecretvalue", json.dumps(advice))
        self.assertTrue(advice["advises_only"])

    def test_bad_reply_and_http_error_fall_back_without_raising(self):
        os.environ["OPENAI_API_KEY"] = "sk-testsecretvalue"

        def garbage(*_args, **_kwargs):
            return 200, {"choices": [{"message": {"content": "hello there"}}]}, {}

        unusable = assist.advise(_item(), transport=garbage)
        self.assertTrue(unusable["fallback"])
        self.assertFalse(unusable["skipped"])
        self.assertIn("Offline heuristic", unusable["summary"])
        self.assertEqual(unusable["recommendation"], "approve")

        def denied(*_args, **_kwargs):
            return 401, {"error": {"message": "bad key sk-testsecretvalue"}}, {}

        failed = assist.advise(_item(), transport=denied)
        self.assertTrue(failed["fallback"])
        self.assertNotIn("sk-testsecretvalue", json.dumps(failed))
        self.assertIn("HTTP 401", failed["reason"])

        def explode(*_args, **_kwargs):
            raise RuntimeError("Authorization Bearer sk-testsecretvalue leaked")

        blown = assist.advise(_item(), transport=explode)
        self.assertTrue(blown["fallback"])
        self.assertNotIn("sk-testsecretvalue", json.dumps(blown))
        self.assertNotIn("Bearer sk-", json.dumps(blown))
        self.assertIn("[redacted]", blown["reason"])

    def test_non_chat_url_does_not_call_transport(self):
        os.environ["OPENAI_API_KEY"] = "sk-testsecretvalue"
        calls = []

        def transport(*_args, **_kwargs):
            calls.append(1)
            raise AssertionError("should not send")

        original = assist.chat_completions_url
        assist.chat_completions_url = lambda: "https://api-m.sandbox.paypal.com/v1/payments/payouts"
        try:
            result = assist.advise(_item(), transport=transport)
        finally:
            assist.chat_completions_url = original
        self.assertEqual(calls, [])
        self.assertTrue(result["fallback"])
        self.assertIn("refusing non-chat path", result["reason"])
        self.assertNotIn("sk-testsecretvalue", json.dumps(result))


class ApproveAndDigestTests(_OfflineGuard):
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
        self._create = paypal.create_invoice_for_item

    def tearDown(self):
        paypal.create_invoice_for_item = self._create
        for name, value in self._saved_paths.items():
            setattr(config, name, value)
        self.tmp.cleanup()
        super().tearDown()

    def _write_scored(self, items: list[dict]) -> None:
        config.SCORED_PATH.write_text(
            json.dumps(
                {
                    "count": len(items),
                    "dropped_count": 0,
                    "scored_at": "2026-10-24T00:00:00+00:00",
                    "items": items,
                }
            ),
            encoding="utf-8",
        )

    def _events(self) -> list[dict]:
        if not config.LEDGER_PATH.exists():
            return []
        return [
            json.loads(line)
            for line in config.LEDGER_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_approve_stores_advice_and_still_skips_paypal(self):
        item = _item()
        self._write_scored([item])
        invoice_calls = []

        def fake_invoice(row):
            invoice_calls.append(row["id"])
            return self._create(row)

        paypal.create_invoice_for_item = fake_invoice
        approved = executor.approve(item["id"])
        self.assertEqual(approved["id"], item["id"])
        self.assertEqual(invoice_calls, [item["id"]])
        pack = (config.VAULT_PACKS_DIR / f"{item['id']}.md").read_text(encoding="utf-8")
        self.assertIn("## AI assist", pack)
        self.assertIn("AI provider skipped", pack)
        self.assertIn("Recommendation: **approve**", pack)
        self.assertIn("advice only", pack)
        self.assertIn("PayPal skipped", pack)
        self.assertNotIn("sk-", pack)
        events = self._events()
        advice = [event for event in events if event["event"] == "ai_advice"]
        self.assertEqual(len(advice), 1)
        self.assertEqual(advice[0]["recommendation"], "approve")
        self.assertTrue(advice[0]["skipped"])
        self.assertTrue(advice[0]["advises_only"])
        self.assertIn("AI provider skipped", advice[0]["summary"])
        state = json.loads(config.STATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(state["approvals"][item["id"]]["status"], "approved")
        self.assertEqual(state["approvals"][item["id"]]["ai"]["recommendation"], "approve")
        self.assertTrue(state["approvals"][item["id"]]["paypal"]["skipped"])

    def test_skip_recommendation_does_not_block_human_approval(self):
        item = _item(
            id="risky0000001",
            title="Connect wallet and send fee",
            summary="Guaranteed profit if you pay the processing fee.",
            risk=0.6,
            effort=0.2,
            score=30,
            category="crypto-airdrop",
        )
        self._write_scored([item])
        calls = []

        def fake_invoice(row):
            calls.append(row["id"])
            return {
                "skipped": True,
                "reason": paypal.skip_reason(),
                "mode": "sandbox",
            }

        paypal.create_invoice_for_item = fake_invoice
        approved = executor.approve(item["id"])
        self.assertEqual(approved["id"], item["id"])
        self.assertEqual(calls, [item["id"]])
        state = json.loads(config.STATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(state["approvals"][item["id"]]["status"], "approved")
        self.assertEqual(state["approvals"][item["id"]]["ai"]["recommendation"], "skip")
        pack = (config.VAULT_PACKS_DIR / f"{item['id']}.md").read_text(encoding="utf-8")
        self.assertIn("Recommendation: **skip**", pack)
        self.assertIn("AI provider skipped", pack)
        self.assertIn("PayPal skipped", pack)

    def test_advise_command_does_not_approve_or_invoice(self):
        item = _item()
        self._write_scored([item])
        calls = []
        paypal.create_invoice_for_item = lambda row: calls.append(row["id"])
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            advice = executor.advise_item(item["id"])
        text = buffer.getvalue()
        self.assertEqual(advice["recommendation"], "approve")
        self.assertIn("AI provider skipped", text)
        self.assertIn("did not approve the item", text)
        self.assertIn("did not create or pay", text)
        self.assertEqual(calls, [])
        self.assertFalse((config.VAULT_PACKS_DIR / f"{item['id']}.md").exists())
        self.assertNotIn("item_approved", [event["event"] for event in self._events()])
        state = ledger.load_state()
        self.assertEqual(state["approvals"], {})

    def test_digest_prints_and_stores_highlight_advice_without_approving(self):
        first = _item()
        second = _item(
            id="second000001",
            title="Second worldwide bonus",
            url="https://example.com/bonus",
            score=60,
            effort=0.7,
            risk=0.1,
        )
        self._write_scored([first, second])
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            top = executor.digest(top_n=2)
        self.assertEqual([row["id"] for row in top], [first["id"], second["id"]])
        text = buffer.getvalue()
        self.assertIn("AI provider skipped", text)
        self.assertIn("建議批准 approve", text)
        self.assertIn("不會自動批准或付款", text)
        note = (config.VAULT_NOTES_DIR / "2026-10-24.md").read_text(encoding="utf-8")
        self.assertIn("AI provider skipped", note)
        self.assertIn("建議批准 approve", note)
        self.assertIn(first["id"], note)
        state = json.loads(config.STATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(state["approvals"], {})
        self.assertEqual(state["items_cache"][first["id"]]["ai"]["provider"], "offline-heuristic")
        self.assertNotIn("item_approved", [event["event"] for event in self._events()])
        self.assertIn("digest_sent", [event["event"] for event in self._events()])


if __name__ == "__main__":
    unittest.main()
