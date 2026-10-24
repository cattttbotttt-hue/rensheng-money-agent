"""Offline tests for signup-bonus category scoring rules.

Run from the repo root:  python3 -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from money_bot.score import GEO_DROP_REASON, score_item  # noqa: E402

TODAY = date(2026, 10, 24)


def item(
    *,
    title: str,
    summary: str = "",
    category: str = "signup-bonus",
    source_id: str = "reddit-crypto-referralcodes",
) -> dict:
    return {
        "id": "sub000000001",
        "title": title,
        "url": "https://example.com/offer",
        "source": "Fixture",
        "source_id": source_id,
        "category": category,
        "summary": summary,
        "extra": {},
    }


class SignupBonusScoringTests(unittest.TestCase):
    def test_no_crypto_airdrop_base_risk(self):
        it = item(
            title="Exchange welcome bonus",
            summary="Sign up and claim your referral reward.",
        )
        out = score_item(it, TODAY)
        self.assertFalse(out["dropped"])
        self.assertEqual(out["risk"], 0.0)
        risk_reasons = [r for r in out["reasons"] if "base risk" in r]
        self.assertEqual(risk_reasons, [])

    def test_coinbase_existing_account_not_dropped(self):
        it = item(
            title="Coinbase referral — share your link",
            summary="If you already have a Coinbase account, refer friends for $20 in BTC.",
        )
        out = score_item(it, TODAY)
        self.assertFalse(out["dropped"])

    def test_binance_gate_not_dropped(self):
        it = item(
            title="Binance referral commission",
            summary="Existing Binance users: invite friends for welcome bonus rewards.",
        )
        out = score_item(it, TODAY)
        self.assertFalse(out["dropped"])

    def test_us_only_still_drops(self):
        it = item(
            title="Chime $300 signup bonus",
            summary="US residents only. No purchase necessary.",
        )
        out = score_item(it, TODAY)
        self.assertTrue(out["dropped"])
        self.assertEqual(out["drop_reason"], GEO_DROP_REASON)

    def test_retail_coupon_spend_to_save_drops(self):
        it = item(
            title="$15 OFF food delivery with referral code",
            summary="Use code at checkout for 15% off your first two orders.",
        )
        out = score_item(it, TODAY)
        self.assertTrue(out["dropped"])
        self.assertIn("spend-to-save", out["drop_reason"])

    def test_signup_bonus_effort_lane_reason(self):
        it = item(
            title="App signup bonus $25",
            summary="Register with referral link for new users worldwide.",
        )
        out = score_item(it, TODAY)
        self.assertFalse(out["dropped"])
        self.assertTrue(any("signup-bonus" in r for r in out["reasons"]))

    def test_worldwide_signup_keeps(self):
        it = item(
            title="Neobank welcome bonus",
            summary="Open an account — bonus available worldwide for international customers.",
        )
        out = score_item(it, TODAY)
        self.assertFalse(out["dropped"])


if __name__ == "__main__":
    unittest.main()
