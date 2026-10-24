"""Telegram Bot API clients — one bot per butler (春/夏/秋).

Tokens come from config.telegram_tokens(): TELEGRAM_TOKEN_SPRING / _SUMMER /
_AUTUMN, each falling back to TELEGRAM_BOT_TOKEN, so a single bot reproduces
the original one-bot behavior. With no tokens at all, callers fall back to
console output. Inline keyboards and getUpdates polling always use 秋 (autumn).
Stdlib only.
"""

from __future__ import annotations

import json
import urllib.request

from . import config
from .util import log

STAGE = "秋"
API_BASE = "https://api.telegram.org"
BUTLER_LABELS = {"spring": "春", "summer": "夏", "autumn": "秋"}


def _multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    """Minimal multipart/form-data encoder (stdlib) for sendPhoto."""
    import uuid

    boundary = "moneybot" + uuid.uuid4().hex
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        )
    for key, (filename, data, mime) in files.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; '
            f'filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode()
            + data
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), boundary


class ButlerBot:
    def __init__(self, season: str, token: str, chat_id: str) -> None:
        self.season = season
        self.label = BUTLER_LABELS[season]
        self.token = token
        self.chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def _api(self, method: str, _files: dict | None = None, **params):
        url = f"{API_BASE}/bot{self.token}/{method}"
        if _files:
            body, boundary = _multipart(params, _files)
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
        else:
            req = urllib.request.Request(
                url,
                data=json.dumps(params).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
        with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram {method} error ({self.label}): {payload}")
        return payload.get("result")

    def send_photo(self, photo_path: str, caption: str, reply_markup: dict | None = None) -> dict:
        from pathlib import Path

        params: dict = {"chat_id": self.chat_id, "caption": caption, "parse_mode": "HTML"}
        if reply_markup:
            params["reply_markup"] = json.dumps(reply_markup)
        path = Path(photo_path)
        if not path.is_absolute():
            path = config.VAULT_ROOT / path
        with path.open("rb") as fh:
            data = fh.read()
        return self._api(
            "sendPhoto",
            _files={"photo": (path.name, data, "image/png")},
            **params,
        )

    def get_me(self) -> dict:
        return self._api("getMe")

    def send_message(self, text: str, reply_markup: dict | None = None) -> dict:
        params: dict = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            params["reply_markup"] = reply_markup
        return self._api("sendMessage", **params)

    def send_approval_prompt(self, text: str, item_id: str) -> dict:
        keyboard = {
            "inline_keyboard": [
                [{"text": "✅ 批准參與 Approve", "callback_data": f"appr:{item_id}"}]
            ]
        }
        return self.send_message(text, reply_markup=keyboard)

    def get_updates(self, offset: int = 0) -> list[dict]:
        return self._api(
            "getUpdates",
            offset=offset,
            timeout=0,
            allowed_updates=["callback_query"],
        ) or []

    def answer_callback(self, callback_query_id: str, text: str) -> None:
        try:
            self._api("answerCallbackQuery", callback_query_id=callback_query_id, text=text)
        except Exception as exc:
            log(self.label, f"answerCallbackQuery 失敗 failed: {exc}")


class TelegramTrio:
    """The three butlers. Keyboards and update polling always go through 秋."""

    def __init__(self) -> None:
        config.load_dotenv()
        self.chat_id = config.env("TELEGRAM_CHAT_ID")
        tokens = config.telegram_tokens()
        self.bots = {s: ButlerBot(s, t, self.chat_id) for s, t in tokens.items()}

    @property
    def spring(self) -> ButlerBot:
        return self.bots["spring"]

    @property
    def summer(self) -> ButlerBot:
        return self.bots["summer"]

    @property
    def autumn(self) -> ButlerBot:
        return self.bots["autumn"]

    @property
    def enabled(self) -> bool:
        return any(b.enabled for b in self.bots.values())

    def send(self, season: str, text: str, reply_markup: dict | None = None):
        """Send via one butler; if that bot is unconfigured, print to console."""
        bot = self.bots[season]
        if bot.enabled:
            log(bot.label, f"Telegram → chat {bot.chat_id}")
            return bot.send_message(text, reply_markup=reply_markup)
        log(bot.label, "未設定 token，此則改印 console fallback")
        print(text)
        return None

    def send_approval_prompt(self, text: str, item_id: str):
        bot = self.autumn
        if bot.enabled:
            return bot.send_approval_prompt(text, item_id)
        log(bot.label, "未設定 token，Approve 訊息改印 console fallback")
        print(text)
        print(f"[Approve 按鈕] callback_data=appr:{item_id}")
        return None

    def send_photo(self, photo_path: str, caption: str, reply_markup: dict | None = None):
        """sendPhoto via 秋; console fallback prints caption + path."""
        bot = self.autumn
        if bot.enabled:
            log(bot.label, f"Telegram photo → chat {bot.chat_id}: {photo_path}")
            return bot.send_photo(photo_path, caption, reply_markup=reply_markup)
        log(bot.label, "未設定 token，photo 訊息改印 console fallback")
        print(caption)
        print(f"[截圖 screenshot] {photo_path}")
        return None

    def get_updates(self, offset: int = 0) -> list[dict]:
        return self.autumn.get_updates(offset=offset)

    def answer_callback(self, callback_query_id: str, text: str) -> None:
        self.autumn.answer_callback(callback_query_id, text)
