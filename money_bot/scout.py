"""春 scout — fetch opportunities from public, no-auth sources into raw JSON.

Fetcher types (see sources.json):
  rss     — RSS 2.0 or Atom feeds (airdrop aggregators, sweepstakes blogs, reddit .rss)
  devpost — Devpost's public hackathon listing endpoint (general-public events only)
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from . import config, net
from .util import extract_amounts_usd, item_id, log, strip_html, utc_now_iso

STAGE = "春"

# Hackathons tagged only for skilled professionals are out of scope; these Devpost
# themes mark events realistically open to the general public.
GENERAL_PUBLIC_THEMES = {"beginner friendly", "open ended", "low/no code", "no code"}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(el: ET.Element, *names: str) -> str:
    for child in el:
        if _local(child.tag) in names and child.text:
            return child.text.strip()
    return ""


def _entry_link(el: ET.Element) -> str:
    """RSS <link> text or Atom <link href>; prefers non-reddit outbound links."""
    fallback = ""
    for child in el:
        if _local(child.tag) != "link":
            continue
        href = (child.get("href") or "").strip()
        text = (child.text or "").strip()
        rel = (child.get("rel") or "alternate").lower()
        if href and rel == "alternate" and not fallback:
            fallback = href
        if text and not fallback:
            fallback = text
    return fallback


_REDDIT_HOSTS = ("reddit.com", "www.reddit.com", "redd.it")
_URL_RE = re.compile(r'https?://[^\s"<>\]]+')


def _reddit_outbound_link(content_html: str, permalink: str) -> str:
    """Sweepstakes/giveaway posts link out to the real entry page; dig it out."""
    for url in _URL_RE.findall(content_html or ""):
        url = url.rstrip(").,")
        host = re.sub(r"^https?://", "", url).split("/")[0]
        if not any(host == h or host.endswith("." + h) for h in _REDDIT_HOSTS):
            return url
    return permalink


def _parse_date(text: str) -> str | None:
    if not text:
        return None
    try:
        return parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError):
        return None


def fetch_rss(source: dict) -> list[dict]:
    root = ET.fromstring(net.fetch(source["url"]))
    items: list[dict] = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title = strip_html(_child_text(el, "title"))
        link = _entry_link(el)
        raw_content = _child_text(el, "description", "summary", "content", "encoded")
        summary = strip_html(raw_content)[:600]
        published = _parse_date(_child_text(el, "pubDate", "published", "updated"))
        if "reddit.com" in (source["url"] or ""):
            link = _reddit_outbound_link(raw_content, link)
        if not title or not link:
            continue
        items.append(
            {
                "id": item_id(link, title),
                "title": title,
                "url": link,
                "source": source["name"],
                "source_id": source["id"],
                "category": source["category"],
                "summary": summary,
                "published": published,
                "extra": {},
            }
        )
    return items


def fetch_devpost(source: dict) -> list[dict]:
    payload = net.fetch_json(source["url"])
    items: list[dict] = []
    for h in payload.get("hackathons", []):
        if h.get("invite_only"):
            continue
        themes = {t.get("name", "").lower() for t in h.get("themes", [])}
        if not (themes & GENERAL_PUBLIC_THEMES):
            continue  # skip events that assume professional skills
        title = (h.get("title") or "").strip()
        url = h.get("url") or ""
        if not title or not url:
            continue
        prize_text = strip_html(h.get("prize_amount") or "")
        amounts = extract_amounts_usd(prize_text)
        items.append(
            {
                "id": item_id(url, title),
                "title": title,
                "url": url,
                "source": source["name"],
                "source_id": source["id"],
                "category": source["category"],
                "summary": f"{h.get('displayed_location', {}).get('location', '')} · "
                f"{h.get('submission_period_dates', '')} · prize {prize_text}".strip(" ·"),
                "published": None,
                "extra": {
                    "entry_url": h.get("start_a_submission_url") or url,
                    "prize_usd": max(amounts) if amounts else None,
                    "submission_period": h.get("submission_period_dates"),
                    "deadline": _devpost_end_date(h.get("submission_period_dates") or ""),
                    "open_state": h.get("open_state"),
                    "registrations": h.get("registrations_count"),
                },
            }
        )
    return items


_PERIOD_RE = re.compile(
    r"(?P<m1>[A-Za-z]+)\.?\s+(?P<d1>\d{1,2})(?:,?\s+(?P<y1>20\d{2}))?\s*[-–]\s*"
    r"(?:(?P<m2>[A-Za-z]+)\.?\s+)?(?P<d2>\d{1,2}),?\s+(?P<y2>20\d{2})"
)


def _devpost_end_date(period: str) -> str | None:
    """End date of 'Oct 30 - Dec 04, 2026' / 'Oct 16 - 24, 2026' / 'Aug 23, 2026 - Jan 02, 2027'."""
    if not period:
        return None
    m = _PERIOD_RE.search(period)
    if not m:
        return None
    from datetime import date

    month = (m.group("m2") or m.group("m1") or "")[:3].lower()
    months = {m_: i for i, m_ in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
    try:
        return date(int(m.group("y2")), months[month], int(m.group("d2"))).isoformat()
    except (KeyError, ValueError):
        return None


FETCHERS = {"rss": fetch_rss, "devpost": fetch_devpost}


def run() -> list[dict]:
    config.ensure_data_dirs()
    sources = json.loads(config.SOURCES_PATH.read_text(encoding="utf-8"))["sources"]
    all_items: list[dict] = []
    stats: dict = {"ok": {}, "failed": {}, "disabled": []}
    for source in sources:
        if not source.get("enabled", True):
            log(STAGE, f"略過停用來源 skip disabled: {source['id']}")
            stats["disabled"].append(source["name"])
            continue
        fetcher = FETCHERS.get(source["type"])
        if not fetcher:
            log(STAGE, f"未知來源類型 unknown type '{source['type']}': {source['id']}")
            stats["failed"][source["name"]] = f"unknown type {source['type']}"
            continue
        try:
            items = fetcher(source)
        except Exception as exc:  # one bad source must not kill the pipeline
            log(STAGE, f"來源失敗 source failed: {source['id']}: {exc}")
            stats["failed"][source["name"]] = str(exc)[:100]
            continue
        log(STAGE, f"{source['name']}: 取得 {len(items)} 則 items")
        stats["ok"][source["name"]] = len(items)
        all_items.extend(items)
    payload = {
        "fetched_at": utc_now_iso(),
        "count": len(all_items),
        "items": all_items,
        "sources": stats,
    }
    config.RAW_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(STAGE, f"共 {len(all_items)} 則原始項目 → {config.RAW_PATH.name}")
    return all_items


if __name__ == "__main__":
    run()
