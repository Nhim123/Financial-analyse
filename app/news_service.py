"""Kiểm chứng & tổ chức tin tức.

- Gộp trùng: các bài cùng nội dung (tiêu đề tương đồng) gộp thành một.
- Đối chiếu chéo: đếm số hãng tin độc lập cùng đưa tin -> tin ≥2 nguồn = "đã xác nhận".
- Độ mới: tính tuổi tin, đánh dấu tin cũ (quá NEWS_STALE_HOURS) hoặc thiếu thời gian.
- Độ tin cậy: gắn nhãn hãng tin theo danh bạ NEWS_SOURCES.
"""
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .config import (ASSET_LABELS, NEWS_MAX_PER_CLASS, NEWS_SIM_THRESHOLD,
                     NEWS_SOURCES, NEWS_STALE_HOURS)
from .enrich import assess_impact
from .market_tone import aggregate as tone_aggregate, assess_tone

_STOP = {"the", "a", "an", "to", "of", "in", "on", "for", "and", "or", "is", "are",
         "as", "at", "by", "with", "from", "after", "over", "amid", "into", "its",
         "new", "says", "will", "how", "why", "what", "this", "that", "up", "down"}


def _tokens(title: str) -> set:
    words = re.findall(r"[a-z0-9]+", title.lower())
    return {w for w in words if len(w) > 2 and w not in _STOP}


def _similar(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _age_hours(published: str) -> float:
    try:
        dt = datetime.fromisoformat(published)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
    except Exception:
        return -1.0


def _group_class(items: List[dict], translations: dict) -> List[dict]:
    """Gộp các tin tương đồng trong cùng một lĩnh vực."""
    groups: List[dict] = []
    for it in items:
        toks = _tokens(it["title"])
        placed = False
        for g in groups:
            if _similar(toks, g["_tokens"]) >= NEWS_SIM_THRESHOLD:
                g["members"].append(it)
                g["_tokens"] |= toks
                placed = True
                break
        if not placed:
            groups.append({"_tokens": toks, "members": [it]})

    results = []
    for g in groups:
        members = g["members"]
        sources = sorted({m["source"] for m in members})
        # Bài đại diện: hãng tin uy tín nhất (trust nhỏ nhất), rồi mới nhất.
        rep = min(members, key=lambda m: (
            NEWS_SOURCES.get(m["source"], {}).get("trust", 9),
            -(_parse_ts(m["published"])),
        ))
        published = rep["published"]
        age = _age_hours(published) if published else -1.0
        trust = NEWS_SOURCES.get(rep["source"], {}).get("trust")
        tr = translations.get(rep["link"], {})
        impact = assess_impact(rep["title"], rep.get("summary", ""),
                               corroboration_count=len(sources), source_trust=trust)
        tone = assess_tone(rep["title"], rep.get("summary", ""))
        results.append({
            "title": rep["title"],
            "vi_title": tr.get("vi_title") or rep["title"],
            "link": rep["link"],
            "summary": rep.get("summary", ""),
            "vi_summary": tr.get("vi_summary") or "",
            "source": rep["source"],
            "source_label": NEWS_SOURCES.get(rep["source"], {}).get("label", rep["source"]),
            "trust": trust,
            "published": published,
            "age_hours": round(age, 1) if age >= 0 else None,
            "stale": (age < 0) or (age > NEWS_STALE_HOURS),
            "corroboration_count": len(sources),
            "corroboration_sources": [
                NEWS_SOURCES.get(s, {}).get("label", s) for s in sources
            ],
            "verified": len(sources) >= 2,
            "impact": impact,
            "tone": tone,
        })

    # Sắp xếp: xác nhận nhiều nguồn -> ảnh hưởng mạnh -> mới nhất.
    results.sort(key=lambda r: (
        -r["corroboration_count"],
        -r["impact"]["level"],
        -(_parse_ts(r["published"])),
    ))
    return results[:NEWS_MAX_PER_CLASS]


def _parse_ts(published) -> float:
    try:
        return datetime.fromisoformat(published).timestamp()
    except Exception:
        return 0.0


def build(items: List[dict], translations: Optional[dict] = None) -> Dict[str, dict]:
    """Từ danh sách tin thô -> cấu trúc theo lĩnh vực để hiển thị.

    translations: {link: {'vi_title','vi_summary'}} — bản dịch đã cache (tuỳ chọn).
    """
    translations = translations or {}
    by_class: Dict[str, List[dict]] = {k: [] for k in ASSET_LABELS}
    for it in items:
        for cls in it["classes"]:
            if cls in by_class:
                by_class[cls].append(it)

    order = ["stock", "commodity", "crypto", "fx"]
    out = {}
    for cls in order:
        grouped = _group_class(by_class.get(cls, []), translations)
        if grouped:
            out[cls] = {
                "label": ASSET_LABELS[cls],
                "items": grouped,
                "total": len(grouped),
                "verified": sum(1 for g in grouped if g["verified"]),
                "high_impact": sum(1 for g in grouped if g["impact"]["level"] == 3),
            }
    return out


def for_run(run_id: Optional[str]) -> Dict[str, dict]:
    """Tin của một lần thu thập, kèm bản dịch đã cache — dùng chung cho giao diện và Telegram."""
    if not run_id:
        return {}
    from . import storage  # import muộn: storage không phụ thuộc module này
    items = storage.get_news_items(run_id)
    translations = storage.get_translations([it["link"] for it in items])
    return build(items, translations)


def market_tone(news: Dict[str, dict]) -> Optional[dict]:
    """Tổng hợp chỉ số Dovish/Hawkish từ toàn bộ tin đang hiển thị."""
    all_items = [it for block in news.values() for it in block["items"]]
    return tone_aggregate(all_items) if all_items else None
