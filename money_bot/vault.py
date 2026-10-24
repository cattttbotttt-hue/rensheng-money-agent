"""Write the daily digest as a markdown note under notes/."""

from __future__ import annotations

from . import assist, config
from .util import log

STAGE = "秋"

TAGS = {
    "kyc": "〔要KYC〕",
    "address": "〔要地址〕",
    "basic_info": "〔要基本資料〕",
    "email_only": "〔只需Email〕",
}

TOP_DETAILED = 10


def _tag(item: dict) -> str:
    return TAGS.get(item.get("data_effort", "email_only"), "")


def _prize(item: dict) -> str:
    return f"~${item['prize_usd']:,.0f}" if item.get("prize_usd") else "未知"


def _cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


def write_daily_note(raw_payload: dict, scored_payload: dict) -> str:
    """Render 春/夏/秋 into notes/YYYY-MM-DD.md (overwrites same-day note)."""
    items = scored_payload.get("items", [])
    dropped = scored_payload.get("dropped", [])
    day = (scored_payload.get("scored_at") or "")[:10]
    stats = raw_payload.get("sources", {})
    ok, failed, disabled = stats.get("ok", {}), stats.get("failed", {}), stats.get("disabled", [])

    lines: list[str] = [
        f"# Money Opportunities — {day}",
        "",
        "> 🤖 money-bot 日報 · 春偵察 → 夏評選 → 秋行動 · 人工批准制，AI 只提供建議，bot 絕不代送表單、不付款、不繞 captcha、不做 KYC、不索取 seed phrase",
        "",
        "## 🌱 春・偵察 Scout",
        "",
        f"取得 **{raw_payload.get('count', 0)}** 則原始機會 · 來源正常 **{len(ok)}** · 失敗 **{len(failed)}** · 停用 **{len(disabled)}**",
        "",
        "- ✅ " + (" · ".join(f"{k} ({v})" for k, v in ok.items()) or "無"),
    ]
    if failed:
        lines.append("- ❌ " + " · ".join(f"{k} — {v}" for k, v in failed.items()))
    if disabled:
        lines.append("- ⏸ 停用: " + " · ".join(disabled))

    lines += [
        "",
        "## ☀️ 夏・評選 Scoring",
        "",
        f"保留 **{scored_payload.get('count', 0)}** 則 · 過濾 **{scored_payload.get('dropped_count', 0)}** 則",
        "",
        "**Top 3：**",
    ]
    for rank, item in enumerate(items[:3], 1):
        lines.append(f"{rank}. **{item.get('score', 0):.0f}** 分 {_tag(item)} · {_prize(item)} · [{item['title']}]({item['url']})")
        ai_line = assist.format_line(item.get("ai"), limit=280)
        if ai_line:
            lines.append(f"   - {ai_line}")
    if dropped:
        lines += ["", "<details><summary>過濾明細 dropped items</summary>", ""]
        lines += [f"- ~~{d['title'][:80]}~~ — {d['drop_reason']}" for d in dropped]
        lines += ["", "</details>"]

    lines += ["", f"## 🍂 秋・今日精選 Top {min(TOP_DETAILED, len(items))}", ""]
    for rank, item in enumerate(items[:TOP_DETAILED], 1):
        lines += [
            f"### {rank}. [{item['title']}]({item['url']}) — {item.get('score', 0):.0f} 分 {_tag(item)}",
            "",
            f"- 💰 {_prize(item)} · 費力 {item.get('effort', '?')} · 風險 {item.get('risk', '?')} · ⏰ 截止 {item.get('deadline') or '未知'}",
            f"- 類別 {item['category']} · 來源 {item['source']} · 🆔 `{item['id']}`",
            "- 📝 " + "; ".join(item.get("reasons", [])),
        ]
        ai_line = assist.format_line(item.get("ai"), limit=320)
        if ai_line:
            lines.append(f"- {ai_line}")
        lines.append("")

    if len(items) > TOP_DETAILED:
        lines += [
            f"## 📋 其餘保留項目（{len(items) - TOP_DETAILED}）",
            "",
            "| # | 分 | 標籤 | 項目 | 價值 |",
            "|---|----|------|------|------|",
        ]
        for rank, item in enumerate(items[TOP_DETAILED:], TOP_DETAILED + 1):
            lines.append(
                f"| {rank} | {item.get('score', 0):.0f} | {_tag(item)} | [{_cell(item['title'])}]({item['url']}) | {_prize(item)} |"
            )
        lines.append("")

    config.VAULT_NOTES_DIR.mkdir(parents=True, exist_ok=True)
    path = config.VAULT_NOTES_DIR / f"{day}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    rel = path.relative_to(config.VAULT_ROOT)
    log(STAGE, f"已寫入每日筆記 → {rel}")
    return str(rel)
