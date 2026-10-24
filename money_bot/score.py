"""夏 scorer — effort-first scoring, scam-risk heuristics, dedupe, rank.

Final score (0–100):
    100 * (0.55 * effort + 0.30 * value + 0.15 * urgency) - 60 * risk

  effort  — PRIMARY: low-effort keyword signals vs. high-effort demands.
  value   — secondary: max USD amount found in text, log-scaled. High value
            can compensate for complexity.
  urgency — deadline proximity bonus; expired items are dropped.
  risk    — scam heuristics (seed-phrase/wallet/dm/fee red flags). Anything
            above RISK_DROP_THRESHOLD is filtered as junk.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime

from . import config
from .util import canonical_url, extract_amounts_usd, log, title_fingerprint, utc_now_iso

STAGE = "夏"

RISK_DROP_THRESHOLD = 0.75

LOW_EFFORT_KEYWORDS = (
    "sign up", "signup", "register", "join", "follow", "retweet", "tag a",
    "enter to win", "no purchase necessary", "giveaway", "sweepstakes",
    "free", "bonus", "referral", "gleam", "raffle", "claim", "airdrop",
    "eligibility",
)
HIGH_EFFORT_KEYWORDS = (
    "kyc", "deposit", "purchase required", "minimum spend", "app download",
    "download the app", "stake", "hold", "trade", "essay", "video", "pitch",
    "hackathon", "prototype", "submission", "in-person", "attend",
)

def _keyword_re(words: tuple[str, ...]) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b")

_LOW_EFFORT_RE = _keyword_re(LOW_EFFORT_KEYWORDS)
_HIGH_EFFORT_RE = _keyword_re(HIGH_EFFORT_KEYWORDS)

SCAM_PATTERNS = (
    (r"seed phrase|recovery phrase|private key|mnemonic", 1.0, "索取助記詞/私鑰 asks for seed phrase/private key"),
    (r"send\s+(me\s+)?\d*\s*(eth|btc|usdt|usdc|sol)|advance fee|processing fee", 0.6, "要求先匯款/手續費 asks for upfront crypto/fee"),
    (r"connect (your )?wallet|sync wallet|validate wallet|rectify wallet|verify wallet", 0.55, "要求連接/驗證錢包 wallet-connect bait"),
    (r"t\.me/|telegram (bot|mini ?app)", 0.5, "Telegram 機器人連結 telegram-bot link (classic airdrop scam vector)"),
    (r"guaranteed|100x|risk.?free profit|double your", 0.4, "保證獲利話術 guaranteed-profit language"),
    (r"casino|slots|roulette|\bgambl|\bbetting\b", 0.35, "賭博推廣 gambling promo"),
    (r"\bdm (me|us)\b|direct message|whats\s?app", 0.35, "要求私訊 DM-me pattern"),
    (r"bit\.ly|tinyurl|goo\.gl|cutt\.ly|t\.co/", 0.25, "短網址 URL shortener"),
)
CATEGORY_BASE_RISK = {"crypto-airdrop": 0.25}
MIN_SCORE = 25.0

# Articles/reviews are not actionable opportunities (also guards blog-style feeds).
TITLE_JUNK_RE = re.compile(
    r"\breview\b|^what is |^how to |explained\b|^should you|^is .+ legit|^top \d|^best \d|^the \d+ biggest",
    re.IGNORECASE,
)
# Airdrop listings rarely state a USD value; use a modest prior so curated
# listings are not mistaken for zero-value junk.
UNKNOWN_VALUE_PRIOR = {"crypto-airdrop": 0.3, "signup-bonus": 0.35}

# Spend-to-save / purchase-required offers are hard-dropped (not penalized):
# discounts and purchases are not gains. Guard: never drop items whose text
# carries a "no purchase necessary" disclaimer.
SPEND_TO_SAVE_RE = re.compile(
    r"spend\s+\$|purchase required|minimum spend|buy\b.{0,40}?to enter|"
    r"\d+\s?%\s*off|\$\s?\d[\d,]*\s*off\b|save\s+\$|coupon|promo code|"
    r"discount|when you buy|with purchase|qualifying purchase"
)
NO_PURCHASE_GUARD_RE = re.compile(r"no purchase necessar")

# Bank/broker/card SIGNUP bonuses are real gains for this user, not retail
# discounts: promo-category items whose titles signal account/card acquisition
# are exempt from the spend-to-save drop (scam-risk scoring still applies).
BANK_SIGNUP_RE = re.compile(
    r"sign.?up bonus|welcome bonus|new (account|cardmember)|checking bonus|"
    r"savings bonus|brokerage|credit union|\bchecking\b|\bcredit card\b|"
    r"\bcard\b.{0,20}bonus|bonus.{0,20}(points|miles)|(points|miles).{0,20}bonus",
    re.IGNORECASE,
)

# Geographic eligibility — designed for a Macau resident; hard-drop US/UK/EU/CA/AU-only offers.
GEO_DROP_REASON = "地區不符 not open to Macau"

WORLDWIDE_RE = re.compile(
    r"\bworldwide\b|\binternational\b|\bglobal\b|"
    r"any country|open to all|\banywhere\b|all countries|every country",
    re.IGNORECASE,
)
MACAU_OK_RE = re.compile(
    r"\bmacau\b|\bmacao\b|澳門|hong kong and macau",
    re.IGNORECASE,
)
EXCLUSIVE_GEO_RE = re.compile(
    r"(?:50|fifty)\s+united\s+states|"
    r"united\s+states.{0,80}district\s+of\s+columbia|"
    r"district\s+of\s+columbia.{0,40}puerto\s+rico|"
    r"legal\s+residents?\s+of\s+(?:the\s+)?(?:50|fifty\s+)?united\s+states|"
    r"legal\s+residents?\s+of\s+the\s+united\s+states\b|"
    r"legal\s+residents?\s+of\s+the\s+u\.?s\.?\b|"
    r"open\s+to\s+(?:legal\s+)?residents?\s+of\s+(?:the\s+)?(?:50|fifty\s+)?united\s+states|"
    r"u\.?s\.?\s+residents?\s+only|"
    r"\bu\.?s\.?\s+only\b|"
    r"united\s+states\s+residents?\s+only|"
    r"uk\s+residents?\s+only|"
    r"open\s+to\s+uk\s+residents?\b|"
    r"eu\s+residents?\s+only|"
    r"\bcanada\s+only\b|"
    r"australian\s+residents?\s+only",
    re.IGNORECASE,
)
RESIDENTS_OF_RE = re.compile(
    r"(?:legal\s+)?residents?\s+of\s+([^.;:\n]{5,240})",
    re.IGNORECASE,
)
RESTRICTED_PLACE_RE = re.compile(
    r"united\s+states|u\.?s\.?a?\.?|puerto\s+rico|district\s+of\s+columbia|"
    r"united\s+kingdom|\buk\b|european\s+union|\beu\b|\bcanada\b|\baustralia\b",
    re.IGNORECASE,
)


def _geo_open_to_macau(text: str) -> bool:
    return bool(MACAU_OK_RE.search(text) or WORLDWIDE_RE.search(text))


def _exclusive_residency(text: str) -> bool:
    if EXCLUSIVE_GEO_RE.search(text):
        return True
    for m in RESIDENTS_OF_RE.finditer(text):
        fragment = m.group(1)
        if _geo_open_to_macau(fragment):
            continue
        if RESTRICTED_PLACE_RE.search(fragment):
            return True
    return False


def _geo_drop(item: dict, text: str) -> bool:
    if _geo_open_to_macau(text):
        return False
    if _exclusive_residency(text):
        return True
    # Doctor of Credit and similar promo feeds are US-default unless worldwide.
    if item["category"] == "promo":
        return True
    return False


# Data-effort heuristics (matched against lowercase title+summary, highest
# tier first): how much personal data participation demands.
KYC_RE = re.compile(
    r"\bkyc\b|identity verification|verify your identity|passport|\bids?\b|"
    r"proof of address|\bssn\b|social security|driver'?s licen[cs]e|government.?issued"
)
ADDRESS_RE = re.compile(
    r"(home|shipping|mailing|residential|street)\s+address|address required|"
    r"phone (number\s+)?required|phone verification"
)
BASIC_INFO_RE = re.compile(
    r"full name|first name|last name|your name|name and email|date of birth|"
    r"\bdob\b|create (an )?account|open an account"
)


def _data_effort(text: str) -> str:
    if KYC_RE.search(text):
        return "kyc"
    if ADDRESS_RE.search(text):
        return "address"
    if BASIC_INFO_RE.search(text):
        return "basic_info"
    return "email_only"


_BLOCKLIST: list[str] | None = None


def _blocklist() -> list[str]:
    """User-curated kill list from sources.json for items the feed text can't
    expose (e.g. purchase-required promos spotted by hand)."""
    global _BLOCKLIST
    if _BLOCKLIST is None:
        try:
            cfg = json.loads(config.SOURCES_PATH.read_text(encoding="utf-8"))
            _BLOCKLIST = [b.lower() for b in cfg.get("blocklist", [])]
        except Exception:
            _BLOCKLIST = []
    return _BLOCKLIST

_MONTHS = {m.lower(): i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _parse_deadline(text: str) -> date | None:
    """Best-effort deadline extraction: ISO dates and 'Mon DD, YYYY' styles."""
    if not text:
        return None
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    m = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", text)  # US-style 8/31/2025 or 8/31/25
    if m:
        year = int(m.group(3))
        year = year + 2000 if year < 100 else year
        try:
            return date(year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    for m in re.finditer(
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:\s*[-–]\s*\w+\s*\d{1,2})?,?\s*(20\d{2})",
        text.lower(),
    ):
        try:
            return date(int(m.group(3)), _MONTHS[m.group(1)[:3]], int(m.group(2)))
        except (KeyError, ValueError):
            continue
    return None


def _effort(text: str, category: str) -> tuple[float, list[str]]:
    low = sorted(set(_LOW_EFFORT_RE.findall(text)))
    high = sorted(set(_HIGH_EFFORT_RE.findall(text)))
    score = 0.5 + 0.12 * min(len(low), 3) - 0.15 * min(len(high), 3)
    reasons = []
    if low:
        reasons.append(f"低費力訊號 low-effort×{len(low)} ({', '.join(low[:3])})")
    if high:
        reasons.append(f"高費力訊號 high-effort×{len(high)} ({', '.join(high[:3])})")
    if category == "hackathon" and "hackathon" not in high:
        score -= 0.15
        reasons.append("黑客松需產出作品 hackathon deliverable")
    if category == "signup-bonus":
        score = min(1.0, score + 0.1)
        reasons.append("註冊獎賞來源 lane signup-bonus (low-effort friendly)")
    return max(0.0, min(1.0, score)), reasons


def _risk(text: str, category: str) -> tuple[float, list[str]]:
    risk = CATEGORY_BASE_RISK.get(category, 0.0)
    reasons = [f"{category} 類別基礎風險 base risk {risk}"] if risk else []
    for pattern, weight, why in SCAM_PATTERNS:
        if re.search(pattern, text):
            risk += weight
            reasons.append(f"風險+{weight}: {why}")
    return min(1.0, risk), reasons


def _urgency(deadline: date | None, today: date) -> tuple[float, str | None]:
    if not deadline:
        return 0.4, None
    days = (deadline - today).days
    if days < 0:
        return -1.0, f"已過期 expired {days} 天前"
    if days <= 2:
        return 1.0, f"截止在即 due in {days} 天"
    if days <= 7:
        return 0.8, f"一週內截止 due in {days} 天"
    if days <= 30:
        return 0.6, f"{days} 天後截止"
    return 0.4, f"{days} 天後截止"


def _value(item: dict, text: str) -> tuple[float, float | None, str | None]:
    amounts = extract_amounts_usd(text)
    prize = item.get("extra", {}).get("prize_usd")
    if prize:
        amounts.append(float(prize))
    if not amounts:
        prior = UNKNOWN_VALUE_PRIOR.get(item["category"])
        if prior is not None:
            return prior, None, "價值未知，採類別先驗 unknown-value prior"
        return 0.1, None, None
    best = max(amounts)
    return min(1.0, math.log10(best + 1) / 5), best, f"預估價值 est. ~${best:,.0f}"


def score_item(item: dict, today: date) -> dict | None:
    if TITLE_JUNK_RE.search(item["title"]):
        return {**item, "dropped": True, "drop_reason": "文章/評測類內容 article, not an opportunity"}
    haystack = f"{item['title']} {item.get('summary', '')} {item['url']}".lower()
    if any(b in haystack for b in _blocklist()):
        return {**item, "dropped": True, "drop_reason": "用戶封鎖清單 user blocklist"}
    text = f"{item['title']} {item.get('summary', '')}".lower()
    if _geo_drop(item, text):
        return {**item, "dropped": True, "drop_reason": GEO_DROP_REASON}
    bank_exempt = False
    if SPEND_TO_SAVE_RE.search(text) and not NO_PURCHASE_GUARD_RE.search(text):
        if item["category"] in ("promo", "signup-bonus") and BANK_SIGNUP_RE.search(item["title"]):
            bank_exempt = True  # bank/broker/card signup bonus — a real gain, keep scoring
        else:
            return {
                **item,
                "dropped": True,
                "drop_reason": "消費/折扣型優惠 spend-to-save or purchase-required, not a gain",
            }
    effort, e_reasons = _effort(text, item["category"])
    risk, r_reasons = _risk(text, item["category"])
    value, amount, v_reason = _value(item, text)
    deadline = None
    if item.get("extra", {}).get("deadline"):
        try:
            deadline = date.fromisoformat(item["extra"]["deadline"])
        except ValueError:
            deadline = None
    if not deadline:
        deadline = _parse_deadline(item.get("summary", "") + " " + item["title"])
    urgency, u_reason = _urgency(deadline, today)

    if risk >= RISK_DROP_THRESHOLD:
        return {**item, "dropped": True, "drop_reason": f"風險過高 junk risk={risk:.2f}"}
    if urgency < 0:
        return {**item, "dropped": True, "drop_reason": u_reason}

    final = round(100 * (0.55 * effort + 0.30 * value + 0.15 * urgency) - 60 * risk, 1)
    data_effort = _data_effort(text)
    penalty = 0
    if data_effort == "kyc":
        penalty = config.DATA_EFFORT_PENALTY_KYC
    elif data_effort == "address":
        penalty = config.DATA_EFFORT_PENALTY_ADDRESS
    if penalty:
        final = round(final - penalty, 1)
    if final < MIN_SCORE:
        return {**item, "dropped": True, "drop_reason": f"分數過低 below junk floor score={final}"}
    de_reason = f"資料需求 data effort: {data_effort}" + (f" (−{penalty})" if penalty else "")
    reasons = e_reasons + ([v_reason] if v_reason else []) + ([u_reason] if u_reason else []) + r_reasons + [de_reason]
    if bank_exempt:
        reasons.append("銀行/券商/卡開戶獎勵 bank signup bonus — 豁免消費型過濾 exempt")
    return {
        **item,
        "dropped": False,
        "score": final,
        "data_effort": data_effort,
        "effort": round(effort, 2),
        "risk": round(risk, 2),
        "prize_usd": amount,
        "deadline": deadline.isoformat() if deadline else None,
        "reasons": reasons,
    }


def dedupe(items: list[dict]) -> list[dict]:
    seen_urls: dict[str, str] = {}
    seen_titles: dict[str, str] = {}
    out: list[dict] = []
    for item in items:
        curl = canonical_url(item["url"])
        fp = title_fingerprint(item["title"])
        if curl in seen_urls or (fp and fp in seen_titles):
            continue
        seen_urls[curl] = item["id"]
        if fp:
            seen_titles[fp] = item["id"]
        out.append(item)
    return out


def run() -> list[dict]:
    raw = json.loads(config.RAW_PATH.read_text(encoding="utf-8"))
    items = dedupe(raw.get("items", []))
    log(STAGE, f"去重後 {len(items)}/{raw.get('count', 0)} 則進入評分")
    today = date.today()
    kept, dropped = [], []
    for item in items:
        scored = score_item(item, today)
        (dropped if scored["dropped"] else kept).append(scored)
    for d in dropped:
        log(STAGE, f"過濾 filtered: {d['title'][:60]} — {d['drop_reason']}")
    kept.sort(key=lambda x: x["score"], reverse=True)
    payload = {
        "scored_at": utc_now_iso(),
        "count": len(kept),
        "items": kept,
        "dropped_count": len(dropped),
        "dropped": [
            {"id": d["id"], "title": d["title"], "drop_reason": d["drop_reason"]}
            for d in dropped
        ],
    }
    config.SCORED_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(STAGE, f"保留 {len(kept)} 則，前 3 名: "
        + ", ".join(f"{i['score']:.0f} {i['title'][:40]}" for i in kept[:3]))
    return kept


if __name__ == "__main__":
    run()
