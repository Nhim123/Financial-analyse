"""Tóm tắt tin của NGÀY HÔM QUA (giờ Việt Nam) và gửi Telegram mỗi sáng (mặc định 7:00).

- Lấy mọi tin có giờ đăng trong ngày hôm qua (00:00–24:00 giờ VN) từ các lần thu thập đã lưu,
  kể cả tin đăng tối qua nhưng sáng nay mới thu thập được; gộp trùng & đối chiếu chéo như trang chủ.
- CHỈ đưa tin quan trọng, ảnh hưởng lớn tới thị trường (enrich.market_importance ≥ REPORT_MIN_IMPORTANCE):
  vĩ mô – chính sách, biến động toàn thị trường, tín hiệu Dovish/Hawkish, nhiều nguồn xác nhận; loại tin
  doanh nghiệp nhỏ, tin mềm (giải thưởng, khai trương…).
- Mỗi lĩnh vực (Chứng khoán thế giới · Vàng & Dầu · Tiền mã hoá · CK Việt Nam): số tin quan trọng / tổng,
  chỉ số Dovish/Hawkish của nhóm và tối đa 5 tin điểm cao nhất.
- Đầu bản tin: tổng quan số tin, giọng điệu chính sách tiền tệ cả ngày + tác động ước tính tới các nhóm
  thị trường, giá chốt của các chỉ số chính; cuối bản tin: lịch sự kiện kinh tế hôm nay.
- Mỗi ngày chỉ gửi một lần (khoá `recap:<ngày>` trong bảng notifications); nếu server tắt lúc 7:00 thì
  gửi bù khi server bật lại trong ngày.
"""
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from . import live, marketdata, news_service, notifier, storage
from .collectors.http import get_client, now_iso
from .config import (COLLECT_INTERVAL_SECONDS, DASHBOARD_URL, NEWS_SOURCES, TELEGRAM_ENABLED,
                     TELEGRAM_RECAP_HOUR)
from .enrich import market_importance, translate_vi
from .homepage import ZONE_META, ZONE_ORDER
from .market_tone import aggregate, market_effects

VN_TZ = timezone(timedelta(hours=7))
_WEEKDAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
TOP_PER_GROUP = 5
_REASON_SHORT = {"vĩ mô – chính sách": "vĩ mô", "toàn thị trường": "toàn TT"}
_MK_SHORT = {"ck_tg": "CK TG", "ck_vn": "CK VN", "vang": "Vàng", "dau": "Dầu", "crypto": "Crypto", "usd": "USD"}
# Giá chốt hiển thị đầu bản tin: (key, nhãn, số lẻ)
_PRICES = [("VNINDEX", "VN-Index", 2), ("SPX", "S&P 500", 2), ("BTC", "BTC", 0), ("XAU", "Vàng TG", 1),
           ("SJC", "SJC", 2), ("WTI", "WTI", 2), ("USDVND", "USD/VND", 0)]


def _esc(s) -> str:
    return notifier._esc(s)


def yesterday() -> date:
    return (datetime.now(VN_TZ) - timedelta(days=1)).date()


def _window(day: date) -> Tuple[datetime, datetime]:
    start = datetime.combine(day, time(0, 0), VN_TZ)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def _parse(ts: Optional[str]) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def collect(day: date) -> Tuple[List[dict], int]:
    """Tin thô có giờ đăng trong `day` (giờ VN), loại trùng theo link; kèm số lần thu thập trong ngày."""
    start, end = _window(day)
    rows = storage.db_query(
        "SELECT run_id, source, title, link, published, summary, classes, fetched_at FROM news "
        "WHERE fetched_at >= ? AND fetched_at < ? ORDER BY fetched_at",
        ((start - timedelta(days=2)).isoformat(), (end + timedelta(hours=12)).isoformat()))
    items: Dict[str, dict] = {}
    runs = set()
    for r in rows:
        fetched = _parse(r["fetched_at"])
        if fetched and start <= fetched < end:
            runs.add(r["run_id"])
        when = _parse(r["published"]) or fetched
        if not when or not (start <= when < end) or r["link"] in items:
            continue
        items[r["link"]] = {"source": r["source"], "title": r["title"], "link": r["link"],
                            "published": r["published"] or r["fetched_at"], "summary": r["summary"],
                            "classes": r["classes"].split(",") if r["classes"] else []}
    return list(items.values()), len(runs)


def _rank(n: dict) -> tuple:
    when = _parse(n["published"])
    return (-n["impact"]["level"], -n["corroboration_count"], -(when.timestamp() if when else 0))


def _ensure_vi(picks: List[dict]) -> None:
    """Dịch (và lưu cache) tiêu đề các tin tiếng Anh được chọn mà chưa có bản dịch."""
    todo = [n for n in picks if n["vi_title"] == n["title"]
            and NEWS_SOURCES.get(n["source"], {}).get("lang") != "vi"]
    if not todo:
        return
    records = []
    with get_client() as client:
        for n in todo:
            n["vi_title"] = translate_vi(n["title"], client)
            records.append({"link": n["link"], "vi_title": n["vi_title"],
                            "vi_summary": n.get("vi_summary") or "", "updated_at": now_iso()})
    storage.upsert_translations(records)


def _lean(agg: Optional[dict]) -> Optional[float]:
    if not agg or agg.get("net") is None:
        return None
    return agg["net"] * min(1.0, agg.get("n_signal_items", 0) / 5)


def _tone_label(agg: Optional[dict]) -> str:
    if not agg or agg.get("net") is None:
        return "⚪ Trung lập"
    net = agg["net"]
    name = "Hawkish" if net >= 15 else ("Dovish" if net <= -15 else "Cân bằng")
    return f"{agg['verdict_sym']} {name} {net:+d}"


def build_recap(day: Optional[date] = None) -> str:
    day = day or yesterday()
    raw, n_runs = collect(day)
    news = news_service.build(raw, storage.get_translations([it["link"] for it in raw]))
    expected = 86400 // max(COLLECT_INTERVAL_SECONDS, 60)
    lines = [f"🗞 <b>TÓM TẮT TIN NGÀY {day.strftime('%d/%m/%Y')}</b> ({_WEEKDAYS[day.weekday()]})"]
    if n_runs < expected * 0.8:
        lines.append(f"⚠️ Server chỉ thu thập {n_runs}/{expected} chu kỳ trong ngày — có thể thiếu tin.")

    uniq: Dict[str, dict] = {}
    for block in news.values():
        for n in block["items"]:
            uniq.setdefault(n["link"], n)
    if not uniq:
        lines += ["", "Không có tin nào được thu thập trong ngày này (server có thể đã tắt)."]
        return "\n".join(lines + ["", f"🔗 {_esc(DASHBOARD_URL)}"])

    allv = list(uniq.values())
    high = sum(1 for n in allv if n["impact"]["level"] == 3)
    verified = sum(1 for n in allv if n["verified"])
    lines.append(f"{len(allv)} tin từ {len({n['source'] for n in allv})} nguồn · 🔴 {high} ảnh hưởng cao · "
                 f"✓ {verified} xác nhận ≥2 nguồn")

    # Giọng điệu chính sách tiền tệ cả ngày + tác động ước tính tới các nhóm thị trường.
    tone = aggregate(allv)
    if tone and tone["net"] is None:
        lines += ["", "<b>Chính sách tiền tệ:</b> ⚪ Trung lập — tin hôm qua chưa có tín hiệu Dovish/Hawkish rõ ràng"]
    elif tone:
        conf = tone["confidence_label"].replace("Độ tin cậy: ", "").lower()
        lines += ["", f"<b>Chính sách tiền tệ:</b> {_tone_label(tone)} — {_esc(tone['verdict'])} "
                      f"(độ tin cậy {_esc(conf)}, {tone['n_signal_items']} tin có tín hiệu)"]
        # Chỉ suy ra tác động khi có ≥2 tin mang tín hiệu (1 tiêu đề đơn lẻ chưa đủ cơ sở).
        eff = [e for e in market_effects(_lean(tone)) if e["dir"]] if tone["n_signal_items"] >= 2 else []
        if eff:
            lines.append("Tác động ước tính: " + " · ".join(f"{_MK_SHORT[e['key']]} {e['arrow']}" for e in eff))

    # Giá chốt gần nhất của các chỉ số chính (tại thời điểm gửi).
    q = live.quotes()
    prices = []
    for key, label, dec in _PRICES:
        p = q.get(key)
        if p and p.get("price"):
            chg = p.get("chg")
            prices.append(f"{label} {p['price']:,.{dec}f}" + (f" ({chg:+.2f}%)" if chg is not None else ""))
    if prices:
        lines += ["", "<b>📊 Giá chốt gần nhất:</b> " + " · ".join(prices)]

    # Từng lĩnh vực: CHỈ tin quan trọng, ảnh hưởng lớn tới thị trường (enrich.market_importance ≥ ngưỡng).
    imp_of = {link: market_importance(n) for link, n in uniq.items()}
    total_imp = sum(1 for v in imp_of.values() if v["important"])
    picks, n_imp, shown = {}, {}, set()
    for cls in ZONE_ORDER:  # một tin thuộc nhiều lĩnh vực chỉ hiện ở lĩnh vực đầu tiên
        keep = [n for n in news.get(cls, {}).get("items", []) if imp_of[n["link"]]["important"]]
        n_imp[cls] = len(keep)
        keep = sorted((n for n in keep if n["link"] not in shown),
                      key=lambda n: (-imp_of[n["link"]]["score"],) + _rank(n))[:TOP_PER_GROUP]
        shown.update(n["link"] for n in keep)
        picks[cls] = keep
    _ensure_vi([n for ps in picks.values() for n in ps])
    lines += ["", f"<b>⭐ {total_imp} tin quan trọng</b> có thể ảnh hưởng lớn tới thị trường "
                  f"(đã lọc bỏ {len(allv) - total_imp} tin doanh nghiệp nhỏ, tin mềm, tin ít tác động)"]
    for cls in ZONE_ORDER:
        items = news.get(cls, {}).get("items", [])
        if not items:
            continue
        icon, title = ZONE_META[cls]
        head = (f"{icon} <b>{_esc(title)}</b> — {n_imp[cls]}/{len(items)} tin quan trọng · "
                f"{_tone_label(aggregate(items))}")
        if not picks[cls]:
            lines += ["", head, "Không có tin ảnh hưởng lớn tới thị trường"
                                + (" (tin quan trọng đã nêu ở mục trên)." if n_imp[cls] else ".")]
            continue
        lines += ["", head]
        for n in picks[cls]:
            bullet = {3: "🔴", 2: "🟠"}.get(n["impact"]["level"], "⚪")
            when = _parse(n["published"])
            meta = [_esc(n["source_label"])]
            if when:
                meta.append(when.astimezone(VN_TZ).strftime("%H:%M"))
            why = [r for r in imp_of[n["link"]]["reasons"] if r in ("vĩ mô – chính sách", "toàn thị trường")]
            meta += [_REASON_SHORT[r] for r in why]
            if n["verified"]:
                meta.append(f"✓ {n['corroboration_count']} nguồn")
            if n["tone"]["tone"] in ("hawkish", "dovish"):
                meta.append(n["tone"]["sym"])
            lines.append(f"{bullet} <a href=\"{_esc(n['link'])}\">{_esc(n['vi_title'])}</a> — {' · '.join(meta)}")

    # Lịch sự kiện kinh tế hôm nay (giờ VN).
    today = (day + timedelta(days=1)).isoformat()
    events = sorted((e for e in marketdata.snapshot().get("calendar", [])
                     if e["dt"][:10] == today and e["imp"] >= 2), key=lambda e: (-e["imp"], e["dt"]))[:6]
    if events:
        lines += ["", "<b>📅 Sự kiện hôm nay:</b>"]
        for e in sorted(events, key=lambda e: e["dt"]):
            lines.append(f"{'🔴' if e['imp'] == 3 else '🟠'} {e['dt'][11:16]} {_esc(e['country'])} · {_esc(e['vi'])}")

    lines += ["", "<i>🔴 ảnh hưởng cao · 🟠 trung bình · 🦅 Hawkish · 🕊️ Dovish — dữ liệu tham khảo, "
                  "không phải khuyến nghị đầu tư</i>", f"🔗 {_esc(DASHBOARD_URL)}"]
    return "\n".join(lines)


def send_recap(day: Optional[date] = None, force: bool = False) -> dict:
    """Gửi bản tóm tắt; mỗi ngày một lần trừ khi force=True (bấm gửi tay)."""
    if not TELEGRAM_ENABLED:
        return {"ok": False, "error": "Telegram chưa cấu hình (thiếu TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID trong .env)."}
    day = day or yesterday()
    key = f"recap:{day.isoformat()}"
    if not force and not storage.filter_unsent([key]):
        return {"ok": True, "skipped": "đã gửi"}
    r = notifier.send_message(build_recap(day))
    if r.get("ok"):
        storage.mark_sent([key], now_iso())
    return r


def catch_up() -> dict:
    """Server bật sau giờ gửi (vd 7:40) -> gửi bù bản tóm tắt hôm qua nếu chưa gửi (trước 23:00)."""
    if not TELEGRAM_ENABLED or TELEGRAM_RECAP_HOUR is None:
        return {"ok": False, "skipped": "tắt"}
    if not (TELEGRAM_RECAP_HOUR <= datetime.now(VN_TZ).hour < 23):
        return {"ok": False, "skipped": "chưa tới giờ"}
    return send_recap()
