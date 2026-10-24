"""秋 executor+ledger — daily digest, approval polling, participation packs.

Approval-gated drafts only: the bot sends a digest with Approve buttons and
prepares a participation pack when a human approves. It never auto-submits
registrations or payments, never bypasses captchas, never performs KYC steps,
and never requests seed phrases.
"""

from __future__ import annotations

import html
import json

from . import config, ledger, publish, vault
from .telegram import TelegramTrio
from .util import log, utc_now_iso

STAGE = "秋"

CATEGORY_ZH = {
    "promo": "銀行/券商優惠",
    "signup-bonus": "註冊獎賞",
    "sweepstakes": "抽獎",
    "giveaway": "贈品",
    "crypto-airdrop": "加密空投",
    "hackathon": "黑客松",
}

DATA_EFFORT_TAGS = {
    "kyc": "〔要KYC〕",
    "address": "〔要地址〕",
    "basic_info": "〔要基本資料〕",
    "email_only": "〔只需Email〕",
}

CHECK_IN_LINES = {
    "spring": "🌱 春：報到，偵察就緒",
    "summer": "☀️ 夏：報到，評選就緒",
    "autumn": "🍂 秋：報到，日報就緒",
}


def _load_json(path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_scored() -> list[dict]:
    return _load_json(config.SCORED_PATH).get("items", [])


def format_scout_report(payload: dict) -> str:
    """春 message: sources ok/failed + total raw items."""
    if not payload:
        return "🌱 春・偵察報告 Scout report\n（無偵察資料 no scout data — run `scout` first）"
    stats = payload.get("sources", {})
    ok = stats.get("ok", {})
    failed = stats.get("failed", {})
    disabled = stats.get("disabled", [])
    lines = [
        f"🌱 春・偵察報告 Scout report — {payload.get('fetched_at', '')[:10]}",
        f"取得 {payload.get('count', 0)} 則新機會 raw items",
        "✅ 來源正常: " + (", ".join(f"{k} ({v})" for k, v in ok.items()) or "無"),
    ]
    if failed:
        lines.append("❌ 來源失敗: " + ", ".join(f"{k} — {v}" for k, v in failed.items()))
    if disabled:
        lines.append("⏸ 已停用: " + ", ".join(disabled))
    return "\n".join(lines)


def format_score_report(payload: dict) -> str:
    """夏 message: kept/dropped counts + top-3 one-liners."""
    if not payload:
        return "☀️ 夏・評選報告 Scoring report\n（無評分資料 no scoring data — run `score` first）"
    items = payload.get("items", [])
    lines = [
        "☀️ 夏・評選報告 Scoring report",
        f"保留 {payload.get('count', 0)} 則 kept / 過濾 {payload.get('dropped_count', 0)} 則 dropped",
    ]
    for rank, item in enumerate(items[:3], 1):
        prize = f"~${item['prize_usd']:,.0f}" if item.get("prize_usd") else "價值未知"
        lines.append(f"{rank}. {item.get('score', 0):.0f}分 · {prize} · {item['title'][:60]}")
    return "\n".join(lines)


def trio_test() -> None:
    """Send one check-in line per butler (春/夏/秋)."""
    tg = TelegramTrio()
    if not tg.enabled:
        log(STAGE, "未設定任何 Telegram token → 全部 console fallback")
    for season, text in CHECK_IN_LINES.items():
        tg.send(season, html.escape(text))


def format_item(item: dict, rank: int) -> str:
    prize = f"~${item['prize_usd']:,.0f}" if item.get("prize_usd") else "未知"
    deadline = item.get("deadline") or "未知"
    reasons = "; ".join(item.get("reasons", [])[:3])
    tag = DATA_EFFORT_TAGS.get(item.get("data_effort", "email_only"), "")
    return (
        f"#{rank} · {item.get('score', 0):.0f}/100 {tag}\n"
        f"{item['title']}\n"
        f"類別 {CATEGORY_ZH.get(item['category'], item['category'])} · 來源 {item['source']}\n"
        f"💰 {prize} · 費力 {item.get('effort', '?')} · 風險 {item.get('risk', '?')}\n"
        f"⏰ 截止 {deadline}\n"
        f"🔗 {item['url']}\n"
        f"📝 {reasons}\n"
        f"🆔 {item['id']}"
    )


def format_slim_telegram(top: list[dict], kept: int, note_relpath: str) -> str:
    """The single 秋 Telegram message: date, N kept, top-3 one-liners, note pointer."""
    lines = [
        f"🍂 秋・每日精選 — {utc_now_iso()[:10]}",
        f"保留 {kept} 則 · 今日 Top {len(top)}（評分: 低費力優先）",
    ]
    for rank, item in enumerate(top[:3], 1):
        prize = f"~${item['prize_usd']:,.0f}" if item.get("prize_usd") else "價值未知"
        tag = DATA_EFFORT_TAGS.get(item.get("data_effort", "email_only"), "")
        lines.append(f"{rank}. {item.get('score', 0):.0f}分 {tag} {prize} {item['title'][:42]}")
    lines.append(f"📓 完整摘要見 notes/ full digest: {note_relpath}")
    return "\n".join(lines)


def _approve_keyboard(top: list[dict]) -> dict:
    return {
        "inline_keyboard": [
            [{"text": f"✅ #{rank} {item['title'][:30]}", "callback_data": f"appr:{item['id']}"}]
            for rank, item in enumerate(top, 1)
        ]
    }


def digest(top_n: int = config.DIGEST_TOP_N, include_seen: bool = False) -> list[dict]:
    state = ledger.load_state()
    raw_payload = _load_json(config.RAW_PATH)
    scored_payload = _load_json(config.SCORED_PATH)
    items = scored_payload.get("items", [])
    fresh = [i for i in items if include_seen or i["id"] not in state["sent_item_ids"]]
    top = fresh[:top_n]
    tg = TelegramTrio()

    if not top:
        log(STAGE, "無新項目可摘要 no new items to digest")
        return []

    note_relpath = vault.write_daily_note(raw_payload, scored_payload)
    header = (
        f"🍂 秋・每日精選 Daily digest — {utc_now_iso()[:10]}\n"
        f"{len(top)} 則新機會（評分: 低費力優先 effort-first）\n"
        "按 ✅ Approve 產生人工參與包"
    )
    slim = format_slim_telegram(top, scored_payload.get("count", 0), note_relpath)
    if tg.enabled:
        log(STAGE, f"Telegram slim digest → chat {tg.chat_id}（{len(top)} 則按鈕）")
        tg.send("autumn", html.escape(slim), reply_markup=_approve_keyboard(top))
    else:
        log(STAGE, "未設定 Telegram tokens/CHAT_ID → 輸出至 console fallback")
        print(f"\n{'=' * 64}\n{format_scout_report(raw_payload)}\n{'-' * 64}")
        print(format_score_report(scored_payload))
        print(f"{'=' * 64}\n{header}\n{'=' * 64}")
        for rank, item in enumerate(top, 1):
            print(format_item(item, rank))
            print(f"[Approve 按鈕] callback_data=appr:{item['id']}")
            print("-" * 64)
        print("Telegram 精簡版預覽 slim preview:")
        print(slim)
        print("-" * 64)

    now = utc_now_iso()
    for item in top:
        state["sent_item_ids"][item["id"]] = now
        state["items_cache"][item["id"]] = item
    state["last_digest_at"] = now
    ledger.save_state(state)
    ledger.append_ledger(
        "digest_sent",
        count=len(top),
        channel="telegram" if tg.enabled else "console",
        item_ids=[i["id"] for i in top],
        note=note_relpath,
    )
    publish.publish()
    return top


def approve(item_id: str, by: str = "local-cli", tg: TelegramTrio | None = None) -> dict | None:
    state = ledger.load_state()
    if state["approvals"].get(item_id, {}).get("status") == "approved":
        log(STAGE, f"{item_id} 已批准過 already approved, skip")
        return None
    item = state["items_cache"].get(item_id)
    if not item:
        item = next((i for i in _load_scored() if i["id"] == item_id), None)
    if not item:
        log(STAGE, f"找不到項目 item not found: {item_id}")
        return None

    state["approvals"][item_id] = {"status": "approved", "ts": utc_now_iso(), "by": by}
    pack_path = ledger.write_pack(item, approved_by=by)
    ledger.save_state(state)
    ledger.append_ledger("item_approved", item_id=item_id, title=item["title"], by=by)
    ledger.append_ledger("pack_created", item_id=item_id, path=pack_path)
    log(STAGE, f"已產生參與包 pack → {pack_path}（人工執行 manual steps only）")
    if tg and tg.enabled:
        tg.send(
            "autumn",
            html.escape(f"✅ 已批准 approved: {item['title'][:80]}\n"
                        f"📓 參與包 pack: {pack_path}\n"
                        "請人工完成步驟；bot 不會代送任何表單或付款。"),
        )
    publish.publish()
    return item


def poll() -> int:
    tg = TelegramTrio()
    if not tg.autumn.enabled:
        log(STAGE, "未設定秋 Telegram token，略過 poll（可用 `python -m money_bot approve <id>` 本機批准）")
        return 0
    state = ledger.load_state()
    updates = tg.get_updates(offset=state["telegram_update_offset"])
    handled = 0
    for update in updates:
        state["telegram_update_offset"] = max(
            state["telegram_update_offset"], update["update_id"] + 1
        )
        cq = update.get("callback_query") or {}
        data = cq.get("data", "")
        item_id = data.split(":", 1)[1] if ":" in data else ""
        user = (cq.get("from") or {}).get("username") or str((cq.get("from") or {}).get("id"))
        if data.startswith("appr:"):
            log(STAGE, f"收到批准 approval: {item_id} by @{user}")
            tg.answer_callback(cq["id"], "已批准，產生參與包中… Generating pack…")
            approve(item_id, by=f"telegram:@{user}", tg=tg)
            handled += 1
        elif data.startswith("enter_submit:"):
            log(STAGE, f"收到提交確認 enter_submit: {item_id} by @{user}")
            tg.answer_callback(cq["id"], "提交中… Submitting…")
            from . import enter

            enter.handle_submit(item_id, tg=tg, by=f"telegram:@{user}")
            handled += 1
        elif data.startswith("enter_cancel:"):
            log(STAGE, f"收到放棄 enter_cancel: {item_id} by @{user}")
            tg.answer_callback(cq["id"], "已放棄 Cancelled")
            from . import enter

            enter.handle_cancel(item_id, tg=tg, by=f"telegram:@{user}")
            handled += 1
        else:
            continue
    ledger.save_state(state)
    log(STAGE, f"poll 完成，處理 {handled} 個批准 approvals")
    publish.publish()
    return handled


def run_all() -> None:
    from . import score, scout

    scout.run()
    score.run()
    digest()
