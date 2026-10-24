"""Semi-automated registration for APPROVED items (optional Playwright dep).

HARD RULES
- Never types or stores passwords — password fields are never touched.
  Account creation only via "Sign in with Google" buttons on the ORIGINAL
  opportunity domain, and the final click always belongs to the human at
  the Telegram confirm step (run_submit). Without MONEY_BOT_PROFILE_DIR a
  Google sign-in wall defers to manual completion.
- Never fills payment/card fields.
- Captcha / bot-check → screenshot + defer to the human.
- Aborts with a warning if the page leaves the opportunity's registrable
  domain; "Sign in with Google" is only ever clicked on the original domain.
- MONEY_BOT_PROFILE_DIR must point to a CLONE of the Chrome profile (made on
  the owner's machine). Anything that looks like a live Chrome profile is
  refused outright — launching on a live profile can corrupt the session.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from . import config, ledger
from .util import log, utc_now_iso

STAGE = "秋"

PAYMENT_RE = re.compile(
    r"card|cc[-_]?|cvv|cvc|expir|iban|routing|payment|billing|信用卡|卡號", re.IGNORECASE
)
CAPTCHA_RE = re.compile(
    r"captcha|recaptcha|hcaptcha|turnstile|are you a robot|verify you are human|"
    r"challenge-platform|我不是機器人|驗證您是真人",
    re.IGNORECASE,
)
GOOGLE_BTN_RE = re.compile(
    r"sign in with google|continue with google|使用 google|google 登入", re.IGNORECASE
)
SUBMIT_RE = re.compile(
    r"submit|sign ?up|register|enter|join|claim|continue|apply|註冊|報名|提交|確認|參加",
    re.IGNORECASE,
)

# (profile key, field classifier regex) — first match wins; payment guard runs first.
FIELD_MAP = [
    ("PROFILE_EMAIL", re.compile(r"e[-_]?mail", re.IGNORECASE)),
    ("PROFILE_PHONE", re.compile(r"\bphone\b|\btel\b|\btelephone\b|\bmobile\b|手機|電話", re.IGNORECASE)),
    ("PROFILE_TWITTER", re.compile(r"twitter|\bx\b.?handle", re.IGNORECASE)),
    ("PROFILE_TELEGRAM", re.compile(r"telegram", re.IGNORECASE)),
    ("PROFILE_ADDRESS", re.compile(r"address|street|郵寄|地址", re.IGNORECASE)),
    ("PROFILE_NAME_FIRST", re.compile(r"first.?name|given.?name|名字", re.IGNORECASE)),
    ("PROFILE_NAME_LAST", re.compile(r"last.?name|family.?name|sur.?name|姓氏", re.IGNORECASE)),
    ("PROFILE_NAME", re.compile(r"\bname\b|full.?name|姓名", re.IGNORECASE)),
]

_SKIP_TYPES = {"password", "hidden", "submit", "button", "checkbox", "radio", "file", "image", "reset"}


class EnterUnavailable(RuntimeError):
    pass


class DomainGuardAbort(RuntimeError):
    pass


def _playwright():
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError as exc:
        raise EnterUnavailable(
            "playwright 未安裝（optional dependency）— "
            "pip install playwright && playwright install chromium"
        ) from exc
    return sync_playwright


def _assert_not_live_profile(path: Path) -> None:
    """Refuse genuine LIVE Chrome profile dirs — nothing else.

    Two triggers only:
      1. path is inside a real Chrome config location (component-exact match:
         <home>/.config/google-chrome*, <home>/.config/chromium,
         …/Google/Chrome (macOS/Windows), flatpak com.google.chrome);
      2. a SingletonLock file is present, meaning a browser has THIS profile
         open right now.

    A clone directory (any other name/location) always passes — even after
    real Chrome has written "Local State" into it, which is normal after
    the first launch and must NOT trigger refusal.
    """
    resolved = path.expanduser().resolve()
    parts = [p.lower() for p in resolved.parts]
    live_location = False
    for i, part in enumerate(parts):
        if (part.startswith("google-chrome") or part == "chromium") and i > 0 and parts[i - 1] == ".config":
            live_location = True
        if part == "chrome" and i > 0 and parts[i - 1] == "google":
            live_location = True  # macOS …/Google/Chrome, Windows …/Google/Chrome/User Data
        if part == "com.google.chrome":
            live_location = True  # flatpak
    if live_location:
        raise EnterUnavailable(
            f"MONEY_BOT_PROFILE_DIR 指向**系統 Chrome 的 live profile**：{resolved}\n"
            "請先在 mini PC 上**複製** profile 到獨立目錄（例如 "
            "~/.money-bot/chrome-profile/）再指向複製品。Refusing a live profile dir."
        )
    if (resolved / "SingletonLock").exists():
        raise EnterUnavailable(
            f"此 profile 正被一個執行中的 Chrome 使用（偵測到 SingletonLock）：{resolved}\n"
            "請先關閉該 Chrome，或指向另一個閒置的複製品。"
            "A browser is currently running on this profile (SingletonLock present)."
        )


def _reg_domain(host: str) -> str:
    """Approximate registrable domain (last two labels; good enough for ops use)."""
    labels = (host or "").lower().split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else (host or "")


def _check_domain(page_url: str, orig_domain: str) -> None:
    scheme = urlparse(page_url).scheme
    if scheme == "file":
        return  # local fixtures
    host = urlparse(page_url).hostname or ""
    if _reg_domain(host) != orig_domain:
        raise DomainGuardAbort(
            f"頁面跳離原始網域 left registrable domain: {host} (expected {orig_domain})"
        )


def _page_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""


def detect_captcha(page) -> bool:
    if CAPTCHA_RE.search(_page_text(page)):
        return True
    try:
        frames = page.locator("iframe")
        for i in range(min(frames.count(), 10)):
            src = frames.nth(i).get_attribute("src") or ""
            if CAPTCHA_RE.search(src):
                return True
    except Exception:
        pass
    return False


def _classify_field(el) -> str | None:
    attrs = " ".join(
        filter(
            None,
            [
                el.get_attribute("name"),
                el.get_attribute("id"),
                el.get_attribute("placeholder"),
                el.get_attribute("aria-label"),
                el.get_attribute("type"),
            ],
        )
    )
    if PAYMENT_RE.search(attrs):
        return "__payment__"
    for key, pattern in FIELD_MAP:
        if pattern.search(attrs):
            return key
    return None


def fill_form(page, profile: dict[str, str]) -> dict[str, str]:
    """Fill mappable fields from PROFILE_*; returns {field label: profile key used}."""
    filled: dict[str, str] = {}
    name = profile.get("PROFILE_NAME", "")
    name_parts = name.split(None, 1)
    values = dict(profile)
    values["PROFILE_NAME_FIRST"] = name_parts[0] if name_parts else ""
    values["PROFILE_NAME_LAST"] = name_parts[1] if len(name_parts) > 1 else ""
    try:
        elements = page.locator("input:visible, textarea:visible")
        count = min(elements.count(), 60)
    except Exception:
        return filled
    for i in range(count):
        el = elements.nth(i)
        try:
            ftype = (el.get_attribute("type") or "text").lower()
            if ftype in _SKIP_TYPES:
                continue
            if el.get_attribute("readonly") or el.get_attribute("disabled"):
                continue
            key = _classify_field(el)
            if not key or key == "__payment__":
                continue  # unknown fields left alone; payment fields NEVER filled
            value = values.get(key, "")
            if not value:
                continue  # leave empties when the profile value is unset
            label = el.get_attribute("name") or el.get_attribute("id") or key
            el.fill(value)
            filled[label] = key
        except Exception:
            continue
    return filled


def _find_submit(page):
    for selector in (
        "button[type=submit]:visible",
        "input[type=submit]:visible",
        "button:visible",
        "a.button:visible, a.btn:visible",
    ):
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 15)):
                el = loc.nth(i)
                text = (el.inner_text(timeout=1000) or "") + " " + (el.get_attribute("value") or "")
                if SUBMIT_RE.search(text):
                    return el
        except Exception:
            continue
    return None


def _find_google_button(page):
    try:
        loc = page.locator("button:visible, a:visible, [role=button]:visible")
        for i in range(min(loc.count(), 30)):
            el = loc.nth(i)
            try:
                if GOOGLE_BTN_RE.search(el.inner_text(timeout=800) or ""):
                    return el
            except Exception:
                continue
    except Exception:
        pass
    return None


def _make_context(pw):
    """Plain headless chromium by default; persistent CLONED profile when
    MONEY_BOT_PROFILE_DIR is set (mini PC — enables live Google sessions)."""
    config.load_dotenv()
    headed = config.env("MONEY_BOT_HEADED") == "1"
    profile_dir = config.env("MONEY_BOT_PROFILE_DIR")
    if profile_dir:
        path = Path(profile_dir).expanduser()
        _assert_not_live_profile(path)
        log(STAGE, f"使用複製的 Chrome profile（persistent context）: {path}")
        return pw.chromium.launch_persistent_context(str(path), headless=not headed), None
    browser = pw.chromium.launch(headless=not headed)
    return browser.new_context(), browser


def _open_and_guard(page, url: str, orig_domain: str) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(1200)
    _check_domain(page.url, orig_domain)


def run_enter(item: dict) -> dict:
    """Fill phase: open URL, fill from PROFILE_*, screenshot → outcome dict."""
    sync_playwright = _playwright()
    config.ensure_data_dirs()
    shot_dir = config.DATA_DIR / "enter"
    shot_dir.mkdir(parents=True, exist_ok=True)
    url = item.get("extra", {}).get("entry_url") or item["url"]
    orig_domain = _reg_domain(urlparse(url).hostname or "")
    profile = config.profile()
    shot = shot_dir / f"{item['id']}-fill.png"
    result: dict = {
        "item_id": item["id"],
        "url": url,
        "title": item["title"],
        "ts": utc_now_iso(),
        "filled": {},
        "status": "awaiting_confirm",
        "screenshot": str(shot.relative_to(config.VAULT_ROOT)),
    }
    with sync_playwright() as pw:
        context, browser = _make_context(pw)
        try:
            page = context.new_page()
            _open_and_guard(page, url, orig_domain)
            if detect_captcha(page):
                result["status"] = "captcha"
                page.screenshot(path=str(shot), full_page=True)
                return result
            filled = fill_form(page, profile)
            result["filled"] = filled
            if not filled and _find_google_button(page) is not None:
                result["status"] = "needs_google"
            page.screenshot(path=str(shot), full_page=True)
        finally:
            context.close()
            if browser:
                browser.close()
    return result


def run_submit(session: dict) -> dict:
    """Confirm phase (post-✅): reopen, re-fill, click submit, screenshot result."""
    sync_playwright = _playwright()
    shot = config.DATA_DIR / "enter" / f"{session['item_id']}-result.png"
    url = session["url"]
    orig_domain = _reg_domain(urlparse(url).hostname or "")
    profile = config.profile()
    outcome: dict = {"status": "submitted", "screenshot": str(shot.relative_to(config.VAULT_ROOT))}
    with sync_playwright() as pw:
        context, browser = _make_context(pw)
        try:
            page = context.new_page()
            _open_and_guard(page, url, orig_domain)
            if detect_captcha(page):
                outcome["status"] = "captcha"
                page.screenshot(path=str(shot), full_page=True)
                return outcome
            fill_form(page, profile)
            submit = _find_submit(page)
            google_btn = _find_google_button(page)
            if submit is not None:
                submit.click()
            elif google_btn is not None:
                if not config.env("MONEY_BOT_PROFILE_DIR"):
                    outcome["status"] = "needs_google"
                    page.screenshot(path=str(shot), full_page=True)
                    return outcome
                # Only ever clicked on the original domain, with a cloned profile.
                google_btn.click()
            else:
                outcome["status"] = "no_submit_found"
                page.screenshot(path=str(shot), full_page=True)
                return outcome
            page.wait_for_timeout(3000)
            # Google SSO lands on accounts.google.com BY DESIGN when reached via
            # the original-domain button; any other domain hop aborts.
            host = urlparse(page.url).hostname or ""
            if host.endswith("google.com") and google_btn is None:
                raise DomainGuardAbort(f"unexpected redirect to {host}")
            if not host.endswith("google.com"):
                _check_domain(page.url, orig_domain)
            if detect_captcha(page):
                outcome["status"] = "captcha_after_submit"
            page.screenshot(path=str(shot), full_page=True)
            outcome["page_excerpt"] = _page_text(page)[:300]
        finally:
            context.close()
            if browser:
                browser.close()
    return outcome


def present_enter(result: dict, tg) -> None:
    """Send the fill screenshot for confirmation, or print it (console)."""
    import html as _html

    filled = result["filled"]
    lines = [
        f"📝 半自動註冊預填完成 — {result['title'][:60]}",
        f"🔗 {result['url']}",
        f"已預填 {len(filled)} 個欄位（值不顯示）: "
        + (", ".join(f"{k}←{v}" for k, v in filled.items()) or "無"),
    ]
    keyboard = None
    if result["status"] == "awaiting_confirm":
        lines.append("確認無誤再送出；bot 不會自動提交。")
        keyboard = {
            "inline_keyboard": [
                [{"text": "✅ 送出提交", "callback_data": f"enter_submit:{result['item_id']}"}],
                [{"text": "❌ 放棄", "callback_data": f"enter_cancel:{result['item_id']}"}],
            ]
        }
    elif result["status"] == "captcha":
        lines.append("⚠️ 偵測到 captcha/真人驗證 — 請手動完成，bot 不會繞過。")
    elif result["status"] == "needs_google":
        lines.append("⚠️ 需要 Google 登入 — 未設定 MONEY_BOT_PROFILE_DIR，請手動完成。")
    caption = "\n".join(lines)
    if tg and tg.autumn.enabled:
        tg.send_photo(result["screenshot"], _html.escape(caption), reply_markup=keyboard)
    else:
        log(STAGE, "console fallback：enter 預填結果")
        print(caption)
        print(f"[截圖 screenshot] {result['screenshot']}")
        if keyboard:
            print(f"[按鈕] ✅ enter_submit:{result['item_id']} / ❌ enter_cancel:{result['item_id']}")


def handle_submit(item_id: str, tg=None, by: str = "telegram") -> dict | None:
    state = ledger.load_state()
    session = state.get("enter_sessions", {}).get(item_id)
    if not session or session.get("status") != "awaiting_confirm":
        log(STAGE, f"enter_submit: {item_id} 無待確認 session，略過")
        return None
    log(STAGE, f"enter_submit: {item_id} 由 {by} 確認，重新開頁提交中…")
    try:
        outcome = run_submit(session)
    except (EnterUnavailable, DomainGuardAbort) as exc:
        outcome = {"status": "aborted", "error": str(exc)}
    session["status"] = outcome["status"]
    session["result_screenshot"] = outcome.get("screenshot")
    state["enter_sessions"][item_id] = session
    ledger.save_state(state)
    ledger.append_ledger("enter_submitted", item_id=item_id, status=outcome["status"], by=by)
    ledger.append_pack_log(
        item_id, f"- {utc_now_iso()} — 半自動提交 semi-auto submit: {outcome['status']}（by {by}）"
    )
    msg = f"📤 提交結果 submit outcome: {outcome['status']}（{item_id}）"
    if outcome["status"] in ("captcha", "captcha_after_submit"):
        msg += "\n⚠️ 遇到 captcha — 請手動完成。"
    elif outcome["status"] == "aborted":
        msg += f"\n⚠️ 已中止: {outcome.get('error', '')[:200]}"
    if tg and tg.autumn.enabled and outcome.get("screenshot"):
        tg.send_photo(outcome["screenshot"], msg)
    else:
        print(msg + (f"\n[截圖] {outcome.get('screenshot')}" if outcome.get("screenshot") else ""))
    return outcome


def handle_cancel(item_id: str, tg=None, by: str = "telegram") -> None:
    state = ledger.load_state()
    session = state.get("enter_sessions", {}).get(item_id)
    if session:
        session["status"] = "cancelled"
        state["enter_sessions"][item_id] = session
        ledger.save_state(state)
    ledger.append_ledger("enter_cancelled", item_id=item_id, by=by)
    ledger.append_pack_log(item_id, f"- {utc_now_iso()} — 已放棄 enter cancelled（by {by}）")
    log(STAGE, f"enter_cancel: {item_id} 已放棄")
    if tg and tg.autumn.enabled:
        tg.send("autumn", f"❌ 已放棄此註冊 cancelled: {item_id}")


def cli_enter(item_id: str) -> dict | None:
    state = ledger.load_state()
    if state["approvals"].get(item_id, {}).get("status") != "approved":
        log(STAGE, f"{item_id} 尚未批准 — 請先批准（approve）再執行 enter")
        return None
    item = state["items_cache"].get(item_id)
    if not item:
        import json

        scored = json.loads(config.SCORED_PATH.read_text(encoding="utf-8")).get("items", [])
        item = next((i for i in scored if i["id"] == item_id), None)
    if not item:
        log(STAGE, f"找不到項目 item not found: {item_id}")
        return None
    from .telegram import TelegramTrio

    tg = TelegramTrio()
    try:
        result = run_enter(item)
    except EnterUnavailable as exc:
        log(STAGE, f"無法啟動瀏覽器 browser unavailable: {exc}")
        print(str(exc))
        return None
    except DomainGuardAbort as exc:
        log(STAGE, f"網域守衛觸發 domain guard: {exc}")
        ledger.append_ledger("enter_aborted", item_id=item_id, reason=str(exc)[:200])
        return None
    state = ledger.load_state()
    state.setdefault("enter_sessions", {})[item_id] = result
    ledger.save_state(state)
    ledger.append_ledger(
        "enter_filled", item_id=item_id, status=result["status"], filled=len(result["filled"])
    )
    present_enter(result, tg)
    return result
