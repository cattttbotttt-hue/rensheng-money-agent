"""Offline test for publish.py's dirty-tree rebase.

Regression: run() writes the daily note + state (dirty tree), then publish()
must still pull --rebase and push. Builds a throwaway bare remote + clone
in /tmp — no network, no real repo touched.

Run from the repo root:  python3 -m unittest tests.test_publish_offline -v
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from money_bot import config, publish  # noqa: E402


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


class PublishDirtyTreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.remote = root / "remote.git"
        self.work = root / "work"
        git(root, "init", "--bare", "-q", self.remote.name)
        git(root, "clone", "-q", self.remote.name, self.work.name)
        git(self.work, "config", "user.email", "test@example.com")
        git(self.work, "config", "user.name", "test")
        (self.work / "data").mkdir(parents=True)
        (self.work / "notes").mkdir()
        (self.work / "data" / "state.json").write_text("{}", encoding="utf-8")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-q", "-m", "init")
        git(self.work, "branch", "-M", "main")
        git(self.work, "push", "-q", "-u", "origin", "main")
        self._old_root = config.VAULT_ROOT
        self._old_publish = os.environ.get("MONEY_BOT_PUBLISH")
        config.VAULT_ROOT = self.work
        os.environ["MONEY_BOT_PUBLISH"] = "1"

    def tearDown(self):
        config.VAULT_ROOT = self._old_root
        if self._old_publish is None:
            os.environ.pop("MONEY_BOT_PUBLISH", None)
        else:
            os.environ["MONEY_BOT_PUBLISH"] = self._old_publish
        self.tmp.cleanup()

    def test_publish_with_dirty_tree_and_remote_ahead(self):
        # Remote moves ahead (simulating the vault's auto-backup).
        other = Path(self.tmp.name) / "other"
        git(Path(self.tmp.name), "clone", "-q", self.remote.name, other.name)
        git(other, "config", "user.email", "o@example.com")
        git(other, "config", "user.name", "other")
        (other / "note.txt").write_text("remote work", encoding="utf-8")
        git(other, "add", ".")
        git(other, "commit", "-q", "-m", "remote commit")
        git(other, "branch", "-M", "main")
        git(other, "push", "-q", "origin", "main")

        # Local run() dirties tracked state AND adds a new vault note.
        (self.work / "data" / "state.json").write_text('{"dirty": 1}', encoding="utf-8")
        (self.work / "notes" / "2026-10-22.md").write_text("# digest", encoding="utf-8")

        # Plain `pull --rebase` would die here ("cannot rebase: unstaged changes").
        self.assertNotEqual(git(self.work, "pull", "--rebase", "-q").returncode, 0)
        git(self.work, "reset", "--hard", "-q", "HEAD")  # reset for the real test
        (self.work / "notes").mkdir(exist_ok=True)
        (self.work / "data" / "state.json").write_text('{"dirty": 1}', encoding="utf-8")
        (self.work / "notes" / "2026-10-22.md").write_text("# digest", encoding="utf-8")

        self.assertTrue(publish.publish(), "publish() must succeed on a dirty tree")
        # Note + state are committed locally and present on the remote.
        log = git(self.work, "log", "--oneline", "-2").stdout
        self.assertIn("money-bot: notes update", log)
        remote_files = git(self.remote, "-c", "core.quotepath=false", "ls-tree", "-r", "--name-only", "main").stdout
        self.assertIn("notes/2026-10-22.md", remote_files)
        # Tree is clean afterwards; the stranded-note scenario cannot recur.
        self.assertEqual(git(self.work, "status", "--porcelain").stdout.strip(), "")

    def test_nothing_new_skips_quietly(self):
        self.assertFalse(publish.publish())
        self.assertEqual(git(self.work, "status", "--porcelain").stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
