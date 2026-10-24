"""Offline tests for enter.py / gmail.py — local fixtures only.

No real sites, no Telegram, no vault pushes. Browser tests skip cleanly when
the optional playwright dependency is missing.

Run from the repo root:  python3 -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from money_bot import config, enter, gmail  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"

TEST_PROFILE = {
    "PROFILE_NAME": "Test User",
    "PROFILE_EMAIL": "test@example.com",
    "PROFILE_PHONE": "+10000000000",
    "PROFILE_TELEGRAM": "@testuser",
}


def fake_item(url: str) -> dict:
    return {
        "id": "fixture00001",
        "title": "Fixture Sweepstakes",
        "url": url,
        "source": "fixture",
        "source_id": "fixture",
        "category": "sweepstakes",
        "summary": "",
        "extra": {},
    }


class ProfileEnvMixin:
    def setUp(self):
        for key, value in TEST_PROFILE.items():
            os.environ[key] = value


PLAYWRIGHT_OK = importlib.util.find_spec("playwright") is not None


@unittest.skipUnless(PLAYWRIGHT_OK, "optional playwright not installed")
class EnterFixtureTests(ProfileEnvMixin, unittest.TestCase):
    def test_fill_maps_profile_and_skips_sensitive(self):
        item = fake_item((FIXTURES / "registration_form.html").as_uri())
        result = enter.run_enter(item)
        self.assertEqual(result["status"], "awaiting_confirm")
        filled = result["filled"]
        self.assertEqual(filled.get("email"), "PROFILE_EMAIL")
        self.assertEqual(filled.get("first_name"), "PROFILE_NAME_FIRST")
        self.assertEqual(filled.get("last_name"), "PROFILE_NAME_LAST")
        self.assertEqual(filled.get("phone"), "PROFILE_PHONE")
        self.assertEqual(filled.get("telegram"), "PROFILE_TELEGRAM")
        self.assertNotIn("password", filled)  # never touched
        self.assertNotIn("card_number", filled)  # payment fields never filled
        self.assertTrue((config.VAULT_ROOT / result["screenshot"]).exists())

    def test_submit_reaches_success_page(self):
        item = fake_item((FIXTURES / "registration_form.html").as_uri())
        session = enter.run_enter(item)
        outcome = enter.run_submit(session)
        self.assertEqual(outcome["status"], "submitted")
        self.assertIn("successful", outcome.get("page_excerpt", "").lower())
        self.assertTrue((config.VAULT_ROOT / outcome["screenshot"]).exists())

    def test_captcha_defers_to_human(self):
        item = fake_item((FIXTURES / "captcha_form.html").as_uri())
        result = enter.run_enter(item)
        self.assertEqual(result["status"], "captcha")
        self.assertEqual(result["filled"], {})


class GuardTests(unittest.TestCase):
    def test_live_profile_dir_refused(self):
        with self.assertRaises(enter.EnterUnavailable):
            enter._assert_not_live_profile(
                Path.home() / ".config" / "google-chrome" / "Profile 1"
            )
        with self.assertRaises(enter.EnterUnavailable):
            enter._assert_not_live_profile(
                Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "Default"
            )
        with self.assertRaises(enter.EnterUnavailable):
            enter._assert_not_live_profile(Path.home() / ".config" / "chromium" / "Default")
        with self.assertRaises(enter.EnterUnavailable):
            enter._assert_not_live_profile(Path.home() / ".config" / "google-chrome-beta")
        # a properly-named clone elsewhere is fine
        enter._assert_not_live_profile(Path.home() / ".money-bot" / "chrome-profile")

    def test_clone_with_local_state_still_passes(self):
        """Regression: real Chrome writes 'Local State' at the profile root on
        first launch — a sanctioned clone must NOT be refused after that."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            clone = Path(tmp) / "chrome-profile"
            clone.mkdir()
            (clone / "Local State").write_text("{}", encoding="utf-8")
            enter._assert_not_live_profile(clone)  # must not raise

    def test_singleton_lock_refused(self):
        """A profile with an active browser on it (SingletonLock) is refused."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            clone = Path(tmp) / "chrome-profile"
            clone.mkdir()
            (clone / "SingletonLock").write_text("1234", encoding="utf-8")
            with self.assertRaises(enter.EnterUnavailable):
                enter._assert_not_live_profile(clone)

    def test_domain_guard(self):
        with self.assertRaises(enter.DomainGuardAbort):
            enter._check_domain("https://evil.example.net/phish", "example.com")
        enter._check_domain("https://sub.example.com/path", "example.com")  # no raise
        enter._check_domain("file:///tmp/fixture.html", "")  # fixtures exempt


class GmailTests(unittest.TestCase):
    def test_extract_links(self):
        body = (
            "Welcome! Confirm your entry: https://sweep.example.com/confirm?token=abc123 "
            "or visit https://unrelated.org/promo for details. "
            "Verify here: https://accounts.example.com/verify/456."
        )
        links = gmail.extract_links(body, "example.com")
        self.assertIn("https://sweep.example.com/confirm?token=abc123", links)
        self.assertIn("https://accounts.example.com/verify/456", links)
        self.assertNotIn("https://unrelated.org/promo", links)

    def test_graceful_noop_without_env(self):
        os.environ.pop("GMAIL_ADDRESS", None)
        os.environ.pop("GMAIL_APP_PASSWORD", None)
        self.assertIsNone(gmail.verify_item(fake_item("https://sweep.example.com/x")))


if __name__ == "__main__":
    unittest.main()
