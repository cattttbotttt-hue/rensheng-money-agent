"""State (JSON) + append-only ledger (JSONL) + participation packs.

HARD RULES enforced here and in executor.py:
  * never auto-submit real registrations or payments — drafts/packs only
  * a PayPal invoice may be created after approval; it is never paid here
  * no captcha bypass, no KYC steps, never request seed phrases/private keys
"""

from __future__ import annotations

import copy
import json
import re

from . import assist, config
from .util import log, utc_now_iso

STAGE = "秋"

SAFETY_BANNER = (
    "> ⚠️ **人工操作 only / manual steps only.** This pack is a draft checklist. "
    "The bot does not submit the opportunity form, pay, or enter card details. "
    "After you approve, it may create one PayPal invoice and will not pay it. "
    "Any AI assist text is advice only and cannot approve, skip, or pay. "
    "Never share a seed phrase, recovery phrase, or private key; anyone asking "
    "is a scam. No captcha bypass, no KYC steps are performed by this bot."
)

_PAYPAL_HEADING = "## PayPal Sandbox invoice"

CHECKLISTS = {
    "crypto-airdrop": [
        "透過官方管道交叉驗證此空投（官網/官方 X 或 Discord），勿信私訊",
        "確認資格條件（持有/互動快照、地區限制）",
        "準備錢包地址（{wallet} — 僅公開地址，絕不提供 seed phrase）",
        "需要社交帳號的任務用 {twitter} / {telegram}",
        "在截止日前完成指定動作並截圖存證",
        "記錄 claim 日期到行事曆",
    ],
    "sweepstakes": [
        "閱讀官方規則：資格、地區、每人限抽次數",
        "以直連進入活動頁並手動填表（{name} / {email}）",
        "確認確認信（check spam）",
        "把截止日加入行事曆提醒",
    ],
    "giveaway": [
        "確認贈品與領取條件（地區/平台限制）",
        "以直連手動完成參加步驟（{name} / {email}）",
        "確認通知信箱並留意詐騙中獎訊息",
    ],
    "promo": [
        "詳讀優惠條款：開戶/存款/ direct deposit 門檻與期限",
        "截圖或留存 offer 頁面（條款可能變動）",
        "手動完成開戶/註冊（{name} / {email} / {phone}），記下帳戶費用與提前關戶罰則",
        "在行事曆標記獎勵入帳檢查日（常見 90 天內）",
    ],
    "signup-bonus": [
        "確認活動仍有效並閱讀官方條款（地區、新用戶定義、存款/交易門檻）",
        "用直連或官方 App 手動註冊／輸入推薦碼（{name} / {email}）",
        "已有 Coinbase／Binance 等帳戶者，確認是否仍符合「新用戶」或推薦獎勵條件",
        "完成必要步驟後截圖存證，並在行事曆標記獎勵入帳檢查日",
    ],
    "hackathon": [
        "在報名頁完成註冊（{name} / {email}；確認個人/團隊規則）",
        "確認主題、資格與提交格式",
        "截止前先送初版，再迭代補強",
        "提交後截圖存證",
    ],
}

DEFAULT_STATE: dict = {
    "telegram_update_offset": 0,
    "sent_item_ids": {},
    "approvals": {},
    "items_cache": {},
    "enter_sessions": {},
    "last_digest_at": None,
}


def load_state() -> dict:
    # Deep copy: DEFAULT_STATE holds nested dicts, and a shallow copy would
    # let one approval mutate the template seen by the next load.
    if config.STATE_PATH.exists():
        state = json.loads(config.STATE_PATH.read_text(encoding="utf-8"))
        merged = copy.deepcopy(DEFAULT_STATE)
        merged.update(state)
        return merged
    return copy.deepcopy(DEFAULT_STATE)


def save_state(state: dict) -> None:
    config.ensure_data_dirs()
    config.STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def append_ledger(event: str, **fields) -> None:
    config.ensure_data_dirs()
    record = {"ts": utc_now_iso(), "event": event, **fields}
    with config.LEDGER_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    log(STAGE, f"ledger += {event} {fields.get('item_id', '')}")


def _paypal_markdown(result: dict) -> str:
    lines = [_PAYPAL_HEADING, ""]
    if result.get("skipped"):
        lines.append(f"- {result.get('reason', 'PayPal skipped. No invoice was created.')}")
        lines.append("- The checklist above is unchanged.")
    elif result.get("error") and not result.get("invoice_id"):
        lines.append(f"- Failed: {result.get('error')}")
        lines.append("- No invoice id was recorded. Approval still stands. The bot did not pay anything.")
    else:
        source = result.get("amount_source")
        if source == "placeholder":
            source_text = "sandbox placeholder (no prize was parsed; default 1.00 USD unless PAYPAL_PLACEHOLDER_AMOUNT is set)"
        else:
            source_text = "parsed prize"
        lines.extend(
            [
                f"- Invoice id: `{result.get('invoice_id')}`",
                f"- Status: `{result.get('status')}`",
                f"- Amount: `{result.get('amount')} {result.get('currency', 'USD')}` ({source_text})",
                f"- Link: {result.get('link')}",
            ]
        )
        if result.get("merchant_link") and result.get("merchant_link") != result.get("link"):
            lines.append(f"- Merchant link: {result.get('merchant_link')}")
        lines.append(f"- Mode: `{result.get('mode', 'sandbox')}`")
        if result.get("send_note"):
            lines.append(f"- Send note: {result.get('send_note')}")
        lines.append("- The bot created this invoice and did not pay it.")
    lines.append("")
    return "\n".join(lines)


def _upsert_paypal_section(item_id: str, result: dict) -> None:
    path = config.VAULT_PACKS_DIR / f"{item_id}.md"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    block = _paypal_markdown(result).rstrip() + "\n"
    if _PAYPAL_HEADING in text:
        start = text.index(_PAYPAL_HEADING)
        rest = text[start + len(_PAYPAL_HEADING):]
        match = re.search(r"\n## ", rest)
        tail = ""
        if match:
            tail = rest[match.start() + 1:]
        text = text[:start].rstrip() + "\n\n" + block
        if tail:
            text += "\n" + tail
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += "\n" + block
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")


def record_advice(item_id: str, advice: dict) -> None:
    """Append the advisory summary. This event does not approve or pay."""
    public = assist.public_fields(advice)
    append_ledger(
        "ai_advice",
        item_id=item_id,
        provider=public.get("provider"),
        model=public.get("model") or "",
        recommendation=public.get("recommendation"),
        summary=public.get("summary"),
        reason=public.get("reason"),
        skipped=bool(public.get("skipped")),
        fallback=bool(public.get("fallback")),
        advises_only=True,
    )


def record_paypal(item_id: str, result: dict) -> None:
    """Append the invoice id/status/link (or a clear skip) to the ledger and pack."""
    if result.get("skipped"):
        append_ledger(
            "paypal_invoice_skipped",
            item_id=item_id,
            reason=result.get("reason", ""),
            mode=result.get("mode", ""),
        )
    elif result.get("error") and not result.get("invoice_id"):
        append_ledger(
            "paypal_invoice_failed",
            item_id=item_id,
            error=result.get("error", ""),
            mode=result.get("mode", ""),
        )
    else:
        append_ledger(
            "paypal_invoice_created",
            item_id=item_id,
            invoice_id=result.get("invoice_id"),
            status=result.get("status"),
            link=result.get("link"),
            merchant_link=result.get("merchant_link"),
            amount=result.get("amount"),
            currency=result.get("currency", "USD"),
            amount_source=result.get("amount_source"),
            mode=result.get("mode"),
        )
    _upsert_paypal_section(item_id, result)


def append_pack_log(item_id: str, line: str) -> None:
    """Append an outcome line to the item's pack note (if the pack exists)."""
    path = config.VAULT_PACKS_DIR / f"{item_id}.md"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    with path.open("a", encoding="utf-8") as fh:
        if "## 操作紀錄 Action log" not in text:
            fh.write("\n## 操作紀錄 Action log\n")
        fh.write(line.rstrip() + "\n")


def _profile_placeholders(profile: dict[str, str]) -> dict[str, str]:
    return {
        "name": profile.get("PROFILE_NAME", "<your name>"),
        "email": profile.get("PROFILE_EMAIL", "<your email>"),
        "phone": profile.get("PROFILE_PHONE", "<your phone>"),
        "address": profile.get("PROFILE_ADDRESS", "<your address>"),
        "wallet": profile.get("PROFILE_WALLET_EVM", "<your EVM address>"),
        "twitter": profile.get("PROFILE_TWITTER", "<your X handle>"),
        "telegram": profile.get("PROFILE_TELEGRAM", "<your Telegram handle>"),
    }


def write_pack(item: dict, approved_by: str, advice: dict | None = None) -> str:
    config.ensure_data_dirs()
    config.VAULT_PACKS_DIR.mkdir(parents=True, exist_ok=True)
    entry_url = item.get("extra", {}).get("entry_url") or item["url"]
    checklist = CHECKLISTS.get(item["category"], CHECKLISTS["sweepstakes"])
    profile = config.profile()
    ctx = _profile_placeholders(profile)
    labels = dict(config.PROFILE_FIELDS)
    if profile:
        profile_lines = [f"- {labels[k]}: `{v}`" for k, v in profile.items()]
    else:
        profile_lines = [
            "（未設定 PROFILE_* — 在 .env 填入後，此處會列出可複製貼上的標準資料）"
        ]
    lines = [
        f"# 參與包 Participation Pack — {item['title']}",
        "",
        SAFETY_BANNER,
        "",
        f"- 直連 Direct link: {entry_url}",
        f"- 來源 Source: {item['source']} ({item['url']})",
        f"- 類別 Category: {item['category']}",
        f"- 資料需求 Data effort: {item.get('data_effort', 'email_only')}",
        f"- 評分 Score: {item.get('score', '?')} · 風險 Risk: {item.get('risk', '?')}",
        f"- 預估價值 Est. value: {('$%s' % format(round(item['prize_usd']), ',')) if item.get('prize_usd') else '未知 unknown'}",
        f"- 截止 Deadline: {item.get('deadline') or '未知 unknown'}",
        f"- 批准 Approved by: {approved_by} at {utc_now_iso()}",
        "",
        "## 你的標準資料 Your standard profile（複製貼上用）",
        *profile_lines,
        "",
        "## 步驟清單 Step checklist",
        *[f"- [ ] {step.format(**ctx)}" for step in checklist],
        "",
        "## 評分理由 Scoring reasons",
        *[f"- {r}" for r in item.get("reasons", [])],
        "",
        *assist.advice_lines(advice),
    ]
    path = config.VAULT_PACKS_DIR / f"{item['id']}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path.relative_to(config.VAULT_ROOT))
