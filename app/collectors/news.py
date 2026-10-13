"""Collector tin tức qua RSS. Trả về danh sách NewsItem đã phân loại theo lĩnh vực."""
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional

from ..config import NEWS_FEEDS, NEWS_KEYWORDS
from ..models import NewsItem
from .http import get_client, now_iso

_ATOM = "{http://www.w3.org/2005/Atom}"

# Biên dịch sẵn regex khớp nguyên từ cho từng lĩnh vực.
_CLASS_RE = {
    cls: re.compile(r"\b(?:" + "|".join(re.escape(k) for k in kws) + r")\b", re.IGNORECASE)
    for cls, kws in NEWS_KEYWORDS.items()
}


def _text(el, tag) -> Optional[str]:
    """Lấy text con <tag>; tránh bug 'Element rỗng là falsy' của ElementTree."""
    child = el.find(tag)
    if child is None:
        child = el.find(_ATOM + tag)
    if child is None or child.text is None:
        return None
    return child.text.strip()


def _link(el) -> Optional[str]:
    val = _text(el, "link")
    if val:
        return val
    # Atom: <link href="...">
    a = el.find(_ATOM + "link")
    if a is not None and a.get("href"):
        return a.get("href")
    return None


def _parse_date(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    raw = raw.strip()
    # RFC 822 (vd: Wed, 01 Jul 2026 12:54:17 GMT)
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        pass
    # ISO 8601 (vd: 2026-07-01T12:00:00Z)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return None


def _classify(title: str, summary: str, default: List[str]) -> List[str]:
    text = f"{title} {summary}"
    found = set(default)
    for cls, rx in _CLASS_RE.items():
        if rx.search(text):
            found.add(cls)
    return list(found)


def _clean(html: Optional[str]) -> str:
    if not html:
        return ""
    return re.sub(r"<[^>]+>", "", html).strip()


def collect_news() -> List[NewsItem]:
    fetched = now_iso()
    out: List[NewsItem] = []
    with get_client() as client:
        for feed in NEWS_FEEDS:
            try:
                r = client.get(feed["url"])
                r.raise_for_status()
                root = ET.fromstring(r.content)  # dùng bytes để tôn trọng encoding khai báo
            except Exception as e:  # noqa: BLE001 — bỏ qua feed lỗi, không làm gãy pipeline
                print(f"[news] feed lỗi {feed['source']} {feed['url']}: {e}")
                continue

            items = root.findall(".//item") or root.findall(f".//{_ATOM}entry")
            for it in items:
                title = _text(it, "title")
                link = _link(it)
                if not title or not link:
                    continue
                summary = _clean(_text(it, "description") or _text(it, "summary"))
                published = _parse_date(
                    _text(it, "pubDate") or _text(it, "published") or _text(it, "updated")
                )
                classes = _classify(title, summary, feed.get("default", []))
                if not classes:
                    continue  # tin tổng hợp không khớp lĩnh vực nào -> bỏ
                out.append(NewsItem(
                    source=feed["source"], title=title, link=link,
                    published=published, summary=summary[:280],
                    classes=classes, fetched_at=fetched,
                ))
    return out
