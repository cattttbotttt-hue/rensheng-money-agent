"""Paths, environment, and tunables."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_PATH = DATA_DIR / "raw_items.json"
SCORED_PATH = DATA_DIR / "scored_items.json"
STATE_PATH = DATA_DIR / "state.json"
LEDGER_PATH = DATA_DIR / "ledger.jsonl"
SOURCES_PATH = BASE_DIR / "sources.json"

# This package is the git repo root. Daily notes and participation packs stay
# inside the repo (notes/). Do not walk up to a parent directory.
VAULT_ROOT = BASE_DIR
VAULT_NOTES_DIR = BASE_DIR / "notes"
VAULT_PACKS_DIR = VAULT_NOTES_DIR / "packs"
PACKS_DIR = VAULT_PACKS_DIR

USER_AGENT = "rensheng-money-agent/0.1 (+https://github.com/cattttbotttt-hue/rensheng-money-agent)"

DIGEST_TOP_N = 8
HTTP_TIMEOUT = 30

# Data-effort scoring penalties (subtracted from the final score).
# KYC is 0 by owner decision: most airdrops require KYC and the owner prefers
# to SEE kyc-tagged items (〔要KYC〕 label kept) and decide personally.
DATA_EFFORT_PENALTY_KYC = 0
DATA_EFFORT_PENALTY_ADDRESS = 10

# Standard profile fields used to pre-fill participation packs. Values live
# only in the local .env (gitignored). Never commit them, and never put a
# seed phrase, private key, or account password in any PROFILE_* field.
PROFILE_FIELDS = [
    ("PROFILE_NAME", "姓名 Name"),
    ("PROFILE_EMAIL", "Email"),
    ("PROFILE_PHONE", "電話 Phone"),
    ("PROFILE_ADDRESS", "地址 Address"),
    ("PROFILE_WALLET_EVM", "EVM 錢包公開地址"),
    ("PROFILE_TWITTER", "Twitter/X 帳號"),
    ("PROFILE_TELEGRAM", "Telegram 帳號"),
]


def load_dotenv() -> None:
    """Tiny .env loader so local runs can use `cp .env.example .env` without deps."""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def telegram_tokens() -> dict[str, str]:
    """Resolve per-butler tokens; any unset butler falls back to TELEGRAM_BOT_TOKEN,
    so a single bot reproduces the original one-bot behavior."""
    load_dotenv()
    single = env("TELEGRAM_BOT_TOKEN")
    return {
        "spring": env("TELEGRAM_TOKEN_SPRING") or single,
        "summer": env("TELEGRAM_TOKEN_SUMMER") or single,
        "autumn": env("TELEGRAM_TOKEN_AUTUMN") or single,
    }


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PACKS_DIR.mkdir(parents=True, exist_ok=True)


def profile() -> dict[str, str]:
    """Set PROFILE_* values (key -> value); empty ones are skipped."""
    load_dotenv()
    return {key: env(key) for key, _ in PROFILE_FIELDS if env(key)}
