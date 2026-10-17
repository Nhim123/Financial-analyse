"""View-model cho trang chủ theo thiết kế 2A "Trung tâm Tin tức & Cảnh báo".

Khác prototype (dữ liệu mô phỏng), toàn bộ số liệu ở đây là dữ liệu THẬT đã qua
kiểm chứng của FinWatch: thẻ chỉ số, ticker, top movers (kèm sparkline từ lịch
sử đồng thuận), cảnh báo (suy ra từ kết quả kiểm chứng + tin ảnh hưởng cao),
heatmap biến động, và dòng tin đã dịch tiếng Việt.
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from . import storage
from .config import ANOMALY_THRESHOLD_PCT

# 6 thẻ chỉ số nổi bật (theo hàng thẻ trong thiết kế)
CARD_KEYS = ["SPX", "DJI", "NDQ", "BTC", "XAU", "WTI"]

# Chip danh mục tin: [nhãn, màu chữ, màu nền] — theo tagMap của design tokens
TAG_MAP = {
    "stock":     ("Chứng khoán", "#0f766e", "#e6f2f0"),
    "commodity": ("Hàng hoá",    "#be123c", "#fdeaef"),
    "crypto":    ("Crypto",      "#b45309", "#fbf0df"),
    "vn":        ("CK Việt Nam", "#b91c1c", "#fde8e8"),
}

_SHORT_NAMES = {
    "SPX": "S&P 500", "DJI": "Dow Jones", "NDQ": "Nasdaq", "FTSE": "FTSE 100",
    "N225": "Nikkei 225", "DAX": "DAX", "XAU": "Vàng", "WTI": "Dầu WTI",
    "BRENT": "Dầu Brent", "BTC": "BTC/USD", "ETH": "ETH/USD", "BNB": "BNB/USD",
    "SOL": "SOL/USD", "XRP": "XRP/USD", "USDEUR": "USD/EUR", "USDGBP": "USD/GBP",
    "USDJPY": "USD/JPY", "USDCNY": "USD/CNY", "USDVND": "USD/VND",
}


# Vùng tin trên màn hình chính: thứ tự, biểu tượng, tiêu đề; số tin hiển thị mỗi vùng.
ZONE_ORDER = ["stock", "commodity", "crypto", "vn"]
ZONE_META = {
    "stock":     ("📈", "Chứng khoán thế giới"),
    "commodity": ("🛢️", "Vàng & Dầu"),
    "crypto":    ("🪙", "Tiền mã hoá"),
    "vn":        ("🇻🇳", "Chứng khoán & Doanh nghiệp Việt Nam"),
}
ZONE_ITEMS = 5

# Crypto được đưa vào bảng Tăng/Giảm mạnh nhất (altcoin bị loại).
MOVER_CRYPTO = {"BTC", "ETH"}

_VN_TZ = timezone(timedelta(hours=7))


def _vn_time(published: Optional[str], age: Optional[float]) -> str:
    """Giờ đăng theo giờ Việt Nam: 'HH:MM' nếu trong ngày, 'dd/mm' nếu cũ hơn."""
    if not published:
        return "--:--"
    try:
        dt = datetime.fromisoformat(published).astimezone(_VN_TZ)
    except ValueError:
        return "--:--"
    return dt.strftime("%H:%M") if age is not None and age < 20 else dt.strftime("%d/%m")


def _zone_item(n: dict) -> dict:
    age = n["age_hours"]
    return {
        "time": _vn_time(n["published"], age),
        "is_new": age is not None and age < 3,
        "source": n["source_label"], "title": n["vi_title"],
        "summary": n["vi_summary"] or n["summary"], "link": n["link"],
        "verified": n["verified"], "impact": n["impact"],
    }


def _fmt(v: Optional[float]) -> str:
    if v is None:
        return "—"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if abs(v) >= 10:
        return f"{v:,.2f}"
    return f"{v:,.4f}"


def _sparkline(values: List[float], w: int = 40, h: int = 16) -> str:
    """Chuỗi points cho <polyline> kích thước w×h từ dãy giá trị."""
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    step = w / (len(values) - 1)
    pts = []
    for i, v in enumerate(values):
        x = i * step
        y = h - 1.5 - (v - lo) / span * (h - 3)
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


def build(results: List[dict], news: Dict[str, dict], tone: Optional[dict],
          live_quotes: Optional[Dict[str, dict]] = None) -> dict:
    by_key = {r["key"]: r for r in results}
    live_quotes = live_quotes or {}

    def _now(r) -> Tuple[Optional[float], Optional[float], bool]:
        """(giá, % trong phiên, đang giao dịch?) — ưu tiên giá trực tiếp, thiếu thì dùng
        kết quả kiểm chứng gần nhất."""
        q = live_quotes.get(r["key"])
        if q:
            return q["price"], q["chg"], q.get("live", False)
        return r["consensus"], r.get("day_chg_pct"), False

    # --- 6 thẻ chỉ số ---
    cards = []
    for k in CARD_KEYS:
        r = by_key.get(k)
        if r:
            price, chg, is_live = _now(r)
            cards.append({
                "key": k, "name": _SHORT_NAMES.get(k, r["name"]), "value": _fmt(price),
                "chg": chg, "live": is_live, "status": r["status"],
            })

    # --- ticker: toàn bộ chỉ số ---
    ticker = []
    for r in results:
        price, chg, _ = _now(r)
        ticker.append({"key": r["key"], "label": _SHORT_NAMES.get(r["key"], r["key"]),
                       "value": _fmt(price), "chg": chg})

    # Chỉ số VN30 / HNX30 (chỉ có ở luồng giá trực tiếp) -> thêm vào ticker
    vn_index = [(g, live_quotes[g]) for g in ("VN30", "HNX30") if g in live_quotes]
    for g, q in vn_index:
        ticker.append({"key": g, "label": g, "value": _fmt(q["price"]), "chg": q["chg"]})

    # --- top movers theo % trong phiên, chia nhóm: Thế giới | VN30 | HNX30 ---
    def _top(rows):
        rows = [x for x in rows if x["chg"] is not None]
        return (sorted((x for x in rows if x["chg"] > 0), key=lambda x: -x["chg"])[:5],
                sorted((x for x in rows if x["chg"] < 0), key=lambda x: x["chg"])[:5])

    world = []
    for r in results:
        if r["asset_class"] == "crypto" and r["key"] not in MOVER_CRYPTO:
            continue  # bỏ altcoin, chỉ giữ BTC & ETH
        price, chg, _ = _now(r)
        world.append({"key": r["key"], "sym": r["key"], "name": _SHORT_NAMES.get(r["key"], r["name"]),
                      "value": _fmt(price), "chg": chg, "vn": False,
                      "spark": _sparkline(storage.consensus_history(r["key"], 24))})
    mover_groups = [{"id": "world", "label": "Thế giới", "rows": world}]
    for g in ("VN30", "HNX30"):
        rows = [{"key": k, "sym": k[3:], "name": q.get("name", k[3:]), "value": f"{q['price'] / 1000:,.2f}",
                 "chg": q["chg"], "vn": True, "spark": ""}
                for k, q in live_quotes.items() if k.startswith("VN:") and q.get("group") == g]
        mover_groups.append({"id": g, "label": g, "rows": rows})
    for mg in mover_groups:
        rows = mg.pop("rows")
        mg["gainers"], mg["losers"] = _top(rows)
        # Bảng đầy đủ cho pop-up "Xem thêm": xếp theo % trong phiên, mã chưa có % ở cuối.
        mg["all"] = sorted(rows, key=lambda x: (x["chg"] is None, -(x["chg"] or 0)))

    # --- heatmap: mọi chỉ số, độ đậm theo |% trong phiên| ---
    heat = []
    for r in results:
        _, chg, _ = _now(r)
        a = min(abs(chg) / 2.5, 1.0) * 0.9 if chg is not None else 0.0
        heat.append({
            "key": r["key"], "name": _SHORT_NAMES.get(r["key"], r["key"]),
            "chg": chg, "alpha": round(a, 3),
            "dark_text": a <= 0.5,
        })
    for g, q in vn_index:
        a = min(abs(q["chg"]) / 2.5, 1.0) * 0.9 if q["chg"] is not None else 0.0
        heat.append({"key": g, "name": g, "chg": q["chg"], "alpha": round(a, 3), "dark_text": a <= 0.5})

    # --- cảnh báo: suy ra từ kiểm chứng số liệu + tin ảnh hưởng cao ---
    alerts = []
    for r in results:
        thr = ANOMALY_THRESHOLD_PCT.get(r["asset_class"], 10.0)
        name = _SHORT_NAMES.get(r["key"], r["name"])
        if r["status"] == "ERROR":
            alerts.append({"level": "KHẨN", "icon": "⛔",
                           "title": f"{name}: không nguồn nào trả dữ liệu",
                           "detail": r["notes"], "rank": 3})
        elif r["change_pct"] is not None and abs(r["change_pct"]) >= thr:
            alerts.append({"level": "KHẨN", "icon": "⚡",
                           "title": f"{name} biến động {r['change_pct']:+.2f}%",
                           "detail": f"Vượt ngưỡng bất thường ±{thr}% so với kỳ trước.", "rank": 3})
        elif r["status"] == "WARN" and r["num_sources"] >= 2:
            alerts.append({"level": "CHÚ Ý", "icon": "⚠",
                           "title": f"{name}: nguồn lệch {r['max_diff_pct']:.2f}%",
                           "detail": r["notes"], "rank": 2})
        elif r["status"] == "WARN":
            alerts.append({"level": "TIN", "icon": "◉",
                           "title": f"{name}: chỉ 1 nguồn hợp lệ",
                           "detail": r["notes"], "rank": 1})
    for block in news.values():
        for n in block["items"]:
            if n["impact"]["level"] == 3 and n["verified"]:
                alerts.append({"level": "CHÚ Ý", "icon": "📰",
                               "title": n["vi_title"][:80],
                               "detail": f"Tin ảnh hưởng cao, {n['corroboration_count']} nguồn xác nhận.",
                               "rank": 2})
    alerts.sort(key=lambda a: -a["rank"])
    alert_count = len(alerts)
    alerts = alerts[:6]

    # --- tin tức phân vùng theo nhóm: luôn đủ 4 vùng, mỗi vùng tin mới nhất trước ---
    groups = []
    for cls in ZONE_ORDER:
        icon, title = ZONE_META[cls]
        _, fg, bg = TAG_MAP[cls]
        items = news.get(cls, {}).get("items", [])
        newest = sorted(items, key=lambda n: n["age_hours"] if n["age_hours"] is not None else 9999)
        groups.append({
            "key": cls, "icon": icon, "title": title, "fg": fg, "bg": bg,
            "total": len(items),
            "verified": sum(1 for n in items if n["verified"]),
            "high": sum(1 for n in items if n["impact"]["level"] == 3),
            "items": [_zone_item(n) for n in newest[:ZONE_ITEMS]],
        })

    now = datetime.now(timezone.utc)
    return {
        "cards": cards, "ticker": ticker, "mover_groups": mover_groups,
        "heat": heat, "alerts": alerts, "alert_count": alert_count,
        "groups": groups, "tone": tone, "generated": now.isoformat(timespec="seconds"),
    }
