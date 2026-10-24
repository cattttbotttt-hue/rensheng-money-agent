"""Offline tests for score.py geographic eligibility (Macau resident).

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
    category: str = "sweepstakes",
    source_id: str = "fixture",
) -> dict:
    return {
        "id": "geo000000001",
        "title": title,
        "url": "https://example.com/promo",
        "source": "Fixture",
        "source_id": source_id,
        "category": category,
        "summary": summary,
        "extra": {},
    }


class GeoEligibilityTests(unittest.TestCase):
    def test_hallmark_us_50_states_drops(self):
        it = item(
            title="Hallmark Channel Countdown To Christmas Sweepstakes 2026",
            summary=(
                "Open to legal residents of the 50 United States, the District of "
                "Columbia, and Puerto Rico. No purchase necessary."
            ),
        )
        out = score_item(it, TODAY)
        self.assertTrue(out["dropped"])
        self.assertEqual(out["drop_reason"], GEO_DROP_REASON)

    def test_worldwide_airdrop_keeps(self):
        it = item(
            title="Example Token Airdrop",
            summary="Join the waitlist — open worldwide to any country.",
            category="crypto-airdrop",
        )
        out = score_item(it, TODAY)
        self.assertFalse(out["dropped"])

    def test_no_geo_text_keeps(self):
        it = item(
            title="Generic Sweepstakes",
            summary="Enter for a chance to win a $500 gift card.",
        )
        out = score_item(it, TODAY)
        self.assertFalse(out["dropped"])

    def test_void_where_prohibited_alone_keeps(self):
        it = item(
            title="Retail Giveaway",
            summary="Void where prohibited. Enter your email to win.",
        )
        out = score_item(it, TODAY)
        self.assertFalse(out["dropped"])

    def test_doctor_of_credit_promo_without_worldwide_drops(self):
        it = item(
            title="Chase Southwest Card 80,000 Point Signup Bonus",
            summary="Targeted offer for new cardmembers.",
            category="promo",
            source_id="doctorofcredit",
        )
        out = score_item(it, TODAY)
        self.assertTrue(out["dropped"])
        self.assertEqual(out["drop_reason"], GEO_DROP_REASON)

    def test_promo_available_worldwide_keeps(self):
        it = item(
            title="Exchange Welcome Bonus",
            summary="New users: bonus available worldwide for international customers.",
            category="promo",
            source_id="doctorofcredit",
        )
        out = score_item(it, TODAY)
        self.assertFalse(out["dropped"])

    def test_bank_signup_does_not_override_geo(self):
        it = item(
            title="E*Trade $400 Checking Signup Bonus",
            summary="Legal residents of the United States only. Welcome bonus for new accounts.",
            category="promo",
        )
        out = score_item(it, TODAY)
        self.assertTrue(out["dropped"])
        self.assertEqual(out["drop_reason"], GEO_DROP_REASON)


if __name__ == "__main__":
    unittest.main()
