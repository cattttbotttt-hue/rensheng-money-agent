"""Optional git publishing of local notes.

Hard-gated behind MONEY_BOT_PUBLISH=1. Leave it unset everywhere else
(cloud VMs included): every call is a silent no-op. Adds ONLY notes/ and
data/. .gitignore keeps .env, participation packs, screenshots, the ledger,
and approval state out of the commit. Pulls --rebase first, never
force-pushes, and skips silently when there is nothing new.
"""

from __future__ import annotations

import subprocess

from . import config
from .util import log, utc_now_iso

STAGE = "秋"
PUBLISH_PATHS = ("notes/", "data/")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=config.VAULT_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )


def publish() -> bool:
    config.load_dotenv()
    if config.env("MONEY_BOT_PUBLISH") != "1":
        return False
    try:
        # --autostash is mandatory: run()/approve() dirty the tree (daily note,
        # ledger, state) BEFORE publish runs, and plain rebase refuses a dirty
        # tree, silently stranding the note. Stash-pop around the rebase.
        pull = _git("pull", "--rebase", "--autostash", "-q")
        if pull.returncode != 0:
            log(STAGE, f"publish: git pull 失敗，略過 push — {pull.stderr.strip()[:200]}")
            return False
        _git("add", "--", *PUBLISH_PATHS)
        if _git("diff", "--cached", "--quiet").returncode == 0:
            return False  # nothing new — skip silently
        commit = _git("commit", "-m", f"money-bot: notes update {utc_now_iso()[:10]} [skip ci]")
        if commit.returncode != 0:
            log(STAGE, f"publish: commit 失敗 — {commit.stderr.strip()[:200]}")
            return False
        push = _git("push")  # never --force
        if push.returncode != 0:
            log(STAGE, f"publish: push 失敗 — {push.stderr.strip()[:200]}")
            return False
        log(STAGE, "publish: 已推送 notes 更新 pushed")
        return True
    except Exception as exc:  # publishing must never crash the pipeline
        log(STAGE, f"publish: 例外略過 (non-fatal): {exc}")
        return False
