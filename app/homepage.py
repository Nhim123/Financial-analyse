"""View-model cho trang chủ theo thiết kế màn 2A "Trung tâm Tin tức & Cảnh báo" (4 tab:
Tổng quan · Cổ phiếu Việt Nam · Giá vàng, dầu, crypto · FX — docs/design/README.md).

Khác prototype (dữ liệu mô phỏng), toàn bộ số liệu ở đây là dữ liệu THẬT: giá trực tiếp
(live.py), dữ liệu chu kỳ chậm (marketdata.py), kết quả kiểm chứng đa nguồn và tin tức đã dịch.
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from . import storage
from .market_tone import aggregate as tone_aggregate, item_effects, market_effects
from .config import (ANOMALY_THRESHOLD_PCT, COMMODITIES, CRYPTO, EXTRA_COMMODITIES, EXTRA_CRYPTO,
                     FX_PAIRS, MOVE_ALERT_PCT, STOCKS, VN_SECTORS)

ACCENT = "#d81e2c"  # đỏ SSI — chỉ dùng cho nhãn/branding, không dùng cho tăng/giảm giá
UP, DN = "#12a150", "#e5484d"

# Chip danh mục tin theo nhóm (dùng chung với Telegram): [nhãn, màu chữ, màu nền]
TAG_MAP = {
    "stock":     ("Toàn cầu",  "#0e7490", "#e2f4f7"),
    "commodity": ("Hàng hóa",  "#be123c", "#fdeaef"),
    "crypto":    ("Crypto",    "#b45309", "#fbf0df"),
    "vn":        ("Cổ phiếu",  ACCENT,    "#e6f2f0"),
}
TAG_MACRO = ("Vĩ mô", "#7c3aed", "#f1ebfd")

_SHORT_NAMES = {
    "SPX": "S&P 500", "DJI": "Dow Jones", "NDQ": "Nasdaq", "FTSE": "FTSE 100",
    "N225": "Nikkei 225", "DAX": "DAX", "XAU": "Vàng TG", "WTI": "Dầu WTI",
    "BRENT": "Dầu Brent", "BTC": "BTC/USD", "ETH": "ETH/USD", "BNB": "BNB/USD",
    "SOL": "SOL/USD", "XRP": "XRP/USD", "USDEUR": "USD/EUR", "USDGBP": "USD/GBP",
    "USDJPY": "USD/JPY", "USDCNY": "USD/CNY", "USDVND": "USD/VND", "SJC": "Vàng SJC",
    "VNINDEX": "VN-Index", "VN30": "VN30", "HNX": "HNX-Index", "HNX30": "HNX30", "UPCOM": "UPCOM",
}

# Vùng tin (thứ tự & tiêu đề) — Telegram dùng để chia nhóm tin nhắn "Tin tức mới".
ZONE_ORDER = ["stock", "commodity", "crypto", "vn"]
ZONE_META = {
    "stock":     ("📈", "Chứng khoán thế giới"),
    "commodity": ("🛢️", "Vàng & Dầu"),
    "crypto":    ("🪙", "Tiền mã hoá"),
    "vn":        ("🇻🇳", "Chứng khoán & Doanh nghiệp Việt Nam"),
}
# Đường dẫn (hash) trang tin riêng của từng lĩnh vực.
FIELD_SLUG = {"stock": "tin-the-gioi", "commodity": "tin-hang-hoa", "crypto": "tin-crypto", "vn": "tin-viet-nam"}
FIELD_SHORT = {"stock": "chứng khoán thế giới", "commodity": "vàng & dầu", "crypto": "tiền mã hoá",
               "vn": "chứng khoán Việt Nam"}

# Crypto được đưa vào bảng Tăng/Giảm mạnh nhất nhóm Thế giới (altcoin bị loại).
MOVER_CRYPTO = {"BTC", "ETH"}

CARD_KEYS = ["VNINDEX", "VN30", "HNX", "SPX", "BTC", "SJC"]
TICKER = [("VNINDEX", "VN-INDEX"), ("VN30", "VN30"), ("HNX", "HNX"), ("UPCOM", "UPCOM"),
          ("SPX", "S&P500"), ("NDQ", "NASDAQ"), ("DJI", "DJIA"), ("N225", "NIKKEI"),
          ("FTSE", "FTSE"), ("DAX", "DAX"), ("BTC", "BTC"), ("ETH", "ETH"), ("SJC", "VÀNG SJC"),
          ("XAU", "VÀNG TG"), ("WTI", "DẦU WTI"), ("BRENT", "BRENT"), ("USDVND", "USD/VND"),
          ("EURUSD", "EUR/USD")]
# Số chữ số thập phân khi hiển thị (mặc định 2). Cổ phiếu VN: giá /1000 (nghìn đồng), 2 số lẻ.
DEC = {"BTC": 0, "ETH": 1, "BNB": 1, "SOL": 2, "XRP": 4, "ADA": 4, "DOGE": 4, "AVAX": 2,
       "XAU": 1, "HG": 3, "NG": 3, "USDEUR": 4, "USDGBP": 4, **{p["key"]: p["dec"] for p in FX_PAIRS}}
COMMOD_TAB = [("SJC", "Vàng SJC", "triệu ₫/lượng"), ("XAU", "Vàng thế giới", "USD/oz"),
              ("WTI", "Dầu WTI", "USD/thùng"), ("BRENT", "Dầu Brent", "USD/thùng")] + \
             [(c["key"], c["name"], c["unit"]) for c in EXTRA_COMMODITIES]
_PIPELINE_KEYS = {i["key"] for i in STOCKS + COMMODITIES + CRYPTO} | {
    "USDEUR", "USDGBP", "USDJPY", "USDCNY", "USDVND"}
_ASSET_CLASS = {**{i["key"]: "stock" for i in STOCKS}, **{i["key"]: "commodity" for i in COMMODITIES},
                **{i["key"]: "crypto" for i in CRYPTO + EXTRA_CRYPTO},
                **{p["key"]: "fx" for p in FX_PAIRS}}
_FX_NEWS = re.compile(r"\b(greenback|forex|currenc(y|ies)|yen|yuan|sterling|fed|federal reserve|"
                      r"ecb|boj|central bank|interest rates?|rate (cut|hike)s?|treasur(y|ies)|yields?|"
                      r"tỷ giá|ngoại tệ|lãi suất|ngân hàng nhà nước|nhnn)\b", re.I)

_VN_TZ = timezone(timedelta(hours=7))
_WEEKDAYS = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]


def _vn_time(published: Optional[str], age: Optional[float]) -> str:
    """Giờ đăng theo giờ Việt Nam: 'HH:MM' nếu trong ngày, 'dd/mm' nếu cũ hơn."""
    if not published:
        return "--:--"
    try:
        dt = datetime.fromisoformat(published).astimezone(_VN_TZ)
    except ValueError:
        return "--:--"
    return dt.strftime("%H:%M") if age is not None and age < 20 else dt.strftime("%d/%m")


def _fmt(v: Optional[float]) -> str:
    """Định dạng tự động (dùng cho Telegram)."""
    if v is None:
        return "—"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if abs(v) >= 10:
        return f"{v:,.2f}"
    return f"{v:,.4f}"


def _num(v: Optional[float], dec: int) -> str:
    return "—" if v is None else f"{v:,.{dec}f}"


def _bil(v: Optional[float], signed: bool = False) -> str:
    """VND -> 'x.xxx tỷ'."""
    if v is None:
        return "—"
    return f"{v / 1e9:{'+' if signed else ''},.0f} tỷ"


def _pts(values: List[float], w: float = 100, h: float = 30) -> str:
    """Toạ độ <polyline> cho sparkline (viewBox 0 0 w h), theo công thức của thiết kế."""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return ""
    mn, mx = min(vals), max(vals)
    r = (mx - mn) or 1.0
    n = len(vals)
    return " ".join(f"{i / (n - 1) * w:.1f},{h - 3 - (v - mn) / r * (h - 6):.1f}" for i, v in enumerate(vals))


def _area(values: List[float], w: float = 700, h: float = 180) -> Dict[str, str]:
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return {"line": "", "area": ""}
    mn, mx = min(vals), max(vals)
    r = (mx - mn) or 1.0
    n = len(vals)
    pts = [(i / (n - 1) * w, h - 6 - (v - mn) / r * (h - 20)) for i, v in enumerate(vals)]
    line = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f} {y:.1f}" for i, (x, y) in enumerate(pts))
    return {"line": line, "area": f"{line} L{w} {h} L0 {h} Z"}


_RE_TAG = re.compile(r"<[^>]*>|[^\s>]*(?:href|target|rel|class|src)=\"[^\"]*\"[^\s>]*>?|https?://[^\s\"]+\"?")


def _clean(text: Optional[str]) -> str:
    """Bỏ mảnh HTML / đường dẫn lẫn trong tóm tắt RSS (vd 'target="_blank" rel="nofollow">')."""
    return re.sub(r"\s{2,}", " ", _RE_TAG.sub(" ", text or "")).strip()


def _ev_result(e: dict) -> dict:
    """Nhãn kết quả sự kiện kinh tế so với kỳ vọng (rỗng nếu chưa công bố)."""
    ev = e.get("eval")
    if not ev:
        return {}
    # Màu theo MỨC BẤT NGỜ (không theo chiều số: thất nghiệp "cao hơn" không phải tin tốt);
    # xanh/đỏ dành cho chiều tác động tới từng thị trường.
    fg, bg = (("#4a5a53", "#eef1ec") if ev["status"] == "inline"
              else ("#c0303a", "#fdeaef") if ev["strong"] else ("#b45309", "#fbf0df"))
    tone = {"hawkish": "🦅 Hawkish", "dovish": "🕊️ Dovish"}.get(ev["tone"] or "", "")
    return {"label": ev["label"], "arrow": {"above": "▲", "below": "▼"}.get(ev["status"], "="),
            "strength": ev["strength"], "dev": ev["dev"], "fg": fg, "bg": bg,
            "tone": tone, "meaning": ev["meaning"],
            "effects": [{"name": x["name"], "arrow": x["arrow"], "why": x.get("why", ""),
                         "col": UP if x["dir"] > 0 else DN} for x in ev["effects"]]}


def _lean(agg: Optional[dict]) -> Optional[float]:
    """Độ nghiêng dùng để suy ra tác động: điểm net co lại khi có ít tin mang tín hiệu
    (1 tin -> 1/5, 2 tin -> 2/5 … ≥5 tin -> giữ nguyên) để vài tiêu đề đơn lẻ không thành 'tác động mạnh'."""
    if not agg or agg.get("net") is None:
        return None
    return agg["net"] * min(1.0, agg.get("n_signal_items", 0) / 5)


def _heat(chg: Optional[float]) -> Dict[str, str]:
    """Nền ô heatmap xanh/đỏ, độ đậm theo |%| (kẹp ±3%), chữ trắng khi nền đậm."""
    t = max(-3.0, min(3.0, chg or 0.0)) / 3
    rgb = "18,161,80" if t >= 0 else "229,72,77"
    return {"bg": f"rgba({rgb},{0.1 + 0.62 * abs(t):.2f})", "txt": "#ffffff" if abs(t) > 0.5 else "#0f1b17"}


def build(results: List[dict], news: Dict[str, dict], tone: Optional[dict],
          live_quotes: Optional[Dict[str, dict]] = None, groups: Optional[Dict[str, list]] = None,
          market: Optional[dict] = None, trade_alerts: Optional[list] = None) -> dict:
    q = live_quotes or {}
    groups = groups or {}
    market = market or {}
    by_key = {r["key"]: r for r in results}
    now = datetime.now(timezone.utc)
    now_vn = now.astimezone(_VN_TZ)

    def price(k):
        """(giá, %, đang giao dịch) — ưu tiên giá trực tiếp, thiếu thì dùng kết quả kiểm chứng."""
        if k in q:
            return q[k]["price"], q[k].get("chg"), q[k].get("live", False)
        r = by_key.get(k)
        return (r["consensus"], r.get("day_chg_pct"), False) if r else (None, None, False)

    _hist_cache: Dict[str, List[float]] = {}

    def hist(k: str) -> List[float]:
        if k not in _hist_cache:
            _hist_cache[k] = storage.consensus_history(k, 36) if k in _PIPELINE_KEYS else []
        return _hist_cache[k]

    def spark(k: str) -> str:
        """Sparkline: nến (crypto, VN) -> lịch sử kiểm chứng -> mẫu giá trực tiếp; nối giá hiện tại."""
        vals = list(market.get("spark", {}).get(k, []))
        same_source = bool(vals)  # nến cùng nguồn với giá trực tiếp -> nối được điểm hiện tại
        if not vals:
            # Mẫu giá trực tiếp (cùng nguồn) ưu tiên khi đã đủ dài; không thì lịch sử kiểm chứng
            # (giá đồng thuận đa nguồn — KHÔNG nối giá trực tiếp vì lệch nguồn tạo bước nhảy giả).
            samples = market.get("samples", {}).get(k, [])
            if len(samples) >= 12:
                vals, same_source = list(samples), True
            elif k in ("EURUSD", "GBPUSD"):
                vals = [1 / v for v in hist("USD" + k[:3]) if v]
            elif k == "EURVND":
                vals = [a / b for a, b in zip(hist("USDVND")[-36:], hist("USDEUR")[-36:]) if b]
            else:
                vals = hist(k)
            if len(vals) < 3:
                vals, same_source = list(samples), True
        p = price(k)[0]
        if k.startswith("VN:") and p is not None:
            p /= 1000  # nến SSI của cổ phiếu tính theo nghìn đồng
        if same_source and p is not None and vals:
            vals = vals + [p]
        return _pts(vals)

    def item(k: str, name: Optional[str] = None, dec: Optional[int] = None) -> dict:
        p, c, lv = price(k)
        d = DEC.get(k, 2) if dec is None else dec
        return {"key": k, "name": name or _SHORT_NAMES.get(k, k), "value": _num(p, d), "chg": c,
                "dec": d, "live": lv}

    vn_open = any(q.get(k, {}).get("live") for k in ("VNINDEX", "VN30"))

    # ---------------- Tổng quan ----------------
    cards = [item(k) for k in CARD_KEYS if price(k)[0] is not None]
    ticker = [{**item(k), "label": lb} for k, lb in TICKER if price(k)[0] is not None]

    sectors = []
    for code, name in VN_SECTORS:
        s = q.get(f"SEC:{code}")
        if s:
            sectors.append({"key": f"SEC:{code}", "name": name, "code": code, "chg": s.get("chg"),
                            **_heat(s.get("chg"))})

    # Top movers: Thế giới | VN30 | HNX30 (+ HOSE trong pop-up "Xem tất cả")
    def vn_row(sym: str) -> Optional[dict]:
        s = q.get(f"VN:{sym}")
        if not s:
            return None
        return {"key": f"VN:{sym}", "sym": sym, "name": s.get("name", sym), "value": _num(s["price"] / 1000, 2),
                "chg": s.get("chg"), "vn": True, "vol": s.get("vol"), "val": s.get("val")}

    world = []
    for r in results:
        if r["asset_class"] == "crypto" and r["key"] not in MOVER_CRYPTO:
            continue  # bỏ altcoin, chỉ giữ BTC & ETH
        if r["asset_class"] == "fx":
            continue
        world.append({**item(r["key"]), "sym": r["key"], "vn": False})
    mover_groups = [{"id": "world", "label": "Thế giới", "rows": world}]
    for g in ("VN30", "HNX30", "HOSE"):
        mover_groups.append({"id": g, "label": g, "rows": [x for x in map(vn_row, groups.get(g, [])) if x]})
    for mg in mover_groups:
        rows = mg.pop("rows")
        withc = [x for x in rows if x["chg"] is not None]
        mg["gainers"] = sorted((x for x in withc if x["chg"] > 0), key=lambda x: -x["chg"])[:5]
        mg["losers"] = sorted((x for x in withc if x["chg"] < 0), key=lambda x: x["chg"])[:5]
        for x in mg["gainers"] + mg["losers"]:
            x["spark"] = spark(x["key"])
        mg["all"] = sorted(rows, key=lambda x: (x["chg"] is None, -(x["chg"] or 0)))
        mg["in_tabs"] = mg["id"] != "HOSE"

    # ---------------- Cổ phiếu Việt Nam ----------------
    hose_rows = [x for x in map(vn_row, groups.get("HOSE", [])) if x]
    board = sorted(hose_rows, key=lambda x: -(x["val"] or 0))[:14]
    for x in board:
        x["spark"] = spark(x["key"])
        v = x["vol"] or 0
        x["vol_str"] = f"{v / 1e6:,.1f}M" if v >= 1e6 else f"{v / 1e3:,.0f}K"

    vni = q.get("VNINDEX", {})
    bu, bf, bd = vni.get("adv") or 0, vni.get("flat") or 0, vni.get("dec") or 0
    bt = (bu + bf + bd) or 1
    breadth = {"up": bu, "flat": bf, "down": bd, "upPct": f"{bu / bt * 100:.1f}%",
               "flatPct": f"{bf / bt * 100:.1f}%", "downPct": f"{bd / bt * 100:.1f}%"}

    ex = [("HOSE", q.get("VNINDEX", {})), ("HNX", q.get("HNX", {})), ("UPCOM", q.get("UPCOM", {}))]
    tot_val = sum(e.get("val") or 0 for _, e in ex)
    tot_qty = sum(e.get("qty") or 0 for _, e in ex)
    session_day = None
    if vni.get("qt"):
        session_day = datetime.fromisoformat(vni["qt"]).astimezone(_VN_TZ).strftime("%Y-%m-%d")
    days = [d for d in market.get("vn_days", []) if session_day is None or d["d"] < session_day]
    prev20 = [d["v"] for d in days[-20:]]
    avg = sum(prev20) / len(prev20) if prev20 else None
    delta = (tot_qty / avg - 1) * 100 if avg and tot_qty else None
    bars_src = [d["v"] for d in days[-15:]] + ([tot_qty] if tot_qty else [])
    bmax = max(bars_src) if bars_src else 1
    liquidity = {
        "total": _bil(tot_val), "vol": f"{tot_qty / 1e6:,.1f} triệu CP",
        "avg": f"{avg / 1e6:,.1f} triệu CP" if avg else "—", "n_avg": len(prev20),
        "delta": None if delta is None else f"{delta:+.1f}% KL so với TB{len(prev20)}",
        "delta_up": (delta or 0) >= 0,
        "bars": [round(v / bmax * 100) for v in bars_src],
        "split": [{"name": n, "val": _bil(e.get("val")),
                   "pct": f"{(e.get('val') or 0) / tot_val * 100:.0f}%" if tot_val else "—"} for n, e in ex],
    }

    hose_q = [q.get(f"VN:{x}", {}) for x in groups.get("HOSE", [])]
    buy = sum((s.get("bu") or 0) * (s.get("avg") or 0) for s in hose_q)
    sell = sum((s.get("sd") or 0) * (s.get("avg") or 0) for s in hose_q)
    fnet = sum((s.get("fbv") or 0) - (s.get("fsv") or 0) for s in hose_q)
    flow = {"buy": _bil(buy), "sell": _bil(sell),
            "buyPct": f"{buy / (buy + sell) * 100:.1f}%" if buy + sell else "50%",
            "sellPct": f"{sell / (buy + sell) * 100:.1f}%" if buy + sell else "50%",
            "fnet": _bil(fnet, signed=True), "fnet_up": fnet >= 0, "has": bool(hose_q)}

    # ---------------- Vàng, dầu, crypto ----------------
    caps = market.get("caps", {})

    def cap_str(v):
        if not v:
            return "—"
        return f"${v / 1e12:,.2f} nghìn tỷ" if v >= 1e12 else f"${v / 1e9:,.1f} tỷ"

    crypto = []
    for c in CRYPTO + EXTRA_CRYPTO:
        if price(c["key"])[0] is None:
            continue
        crypto.append({**item(c["key"], c["name"]), "sym": c["key"], "spark": spark(c["key"]),
                       "cap": cap_str(caps.get(c["key"], {}).get("cap"))})
    btc_hero = {**item("BTC", "Bitcoin"), **_area(market.get("btc", [])),
                "cap": cap_str(caps.get("BTC", {}).get("cap")),
                "vol": cap_str(caps.get("BTC", {}).get("vol"))}
    commod = [{**item(k, name), "unit": unit, "spark": spark(k)}
              for k, name, unit in COMMOD_TAB if price(k)[0] is not None]

    # ---------------- FX ----------------
    bank = market.get("bank", {})
    fx = []
    for p in FX_PAIRS:
        if price(p["key"])[0] is None:
            continue
        b = bank.get(p["vcb"], {})
        fx.append({**item(p["key"], p["name"]), "pair": p["pair"], "spark": spark(p["key"]),
                   "ccy": p["vcb"], "bid": _num(b.get("buy"), 2 if (b.get("buy") or 0) < 1000 else 0),
                   "ask": _num(b.get("sell"), 2 if (b.get("sell") or 0) < 1000 else 0)})

    # ---------------- Tin tức ----------------
    def news_item(n: dict, cls: str) -> dict:
        tone = n.get("tone") or {}
        tag = TAG_MACRO if tone.get("tone") in ("hawkish", "dovish") else TAG_MAP[cls]
        age = n["age_hours"]
        return {"time": _vn_time(n["published"], age), "is_new": age is not None and age < 3,
                "age": age if age is not None else 9999, "tag": tag[0], "tag_fg": tag[1], "tag_bg": tag[2],
                "source": n["source_label"], "title": _clean(n["vi_title"]),
                "summary": _clean(n["vi_summary"] or n["summary"]),
                "link": n["link"], "verified": n["verified"], "impact": n["impact"]["level"],
                "impact_label": n["impact"].get("level_label", ""), "direction": n["impact"].get("direction", ""),
                "n_sources": n.get("corroboration_count", 1),
                "tone": tone.get("tone", "neutral"), "tone_label": tone.get("label", "Trung lập"),
                "tone_sym": tone.get("sym", "⚪"),
                "signals": tone.get("hawkish_signals", []) + tone.get("dovish_signals", []),
                "effects": item_effects(tone),
                "raw": f"{n['title']} {n.get('summary', '')} {n['vi_title']}"}

    all_news: Dict[str, dict] = {}
    by_cls: Dict[str, List[dict]] = {c: [] for c in ZONE_ORDER}
    for cls in ZONE_ORDER:
        for n in news.get(cls, {}).get("items", []):
            it = news_item(n, cls)
            by_cls[cls].append(it)
            all_news.setdefault(it["link"], it)

    def newest(items: List[dict], k: int) -> List[dict]:
        return sorted(items, key=lambda x: x["age"])[:k]

    # Tin theo lĩnh vực: trang chủ hiện 3 tin mới nhất/nhóm, "Xem thêm" mở trang riêng của lĩnh vực
    # (toàn bộ tin + chỉ số Dovish/Hawkish của nhóm + tác động ước tính tới từng nhóm thị trường).
    news_fields = []
    for cls in ZONE_ORDER:
        raw_items = news.get(cls, {}).get("items", [])
        agg = tone_aggregate(raw_items) if raw_items else None
        items = newest(by_cls[cls], 99)
        icon, title = ZONE_META[cls]
        news_fields.append({
            "id": cls, "slug": FIELD_SLUG[cls], "icon": icon, "title": title, "short": FIELD_SHORT[cls],
            "fg": TAG_MAP[cls][1], "bg": TAG_MAP[cls][2], "total": len(items),
            "verified": sum(1 for x in items if x["verified"]), "high": sum(1 for x in items if x["impact"] == 3),
            "items": items, "top": items[:3], "tone": agg,
            "effects": market_effects(_lean(agg))})
    stock_news = newest(by_cls["vn"], 4)
    commod_news = newest(by_cls["commodity"] + by_cls["crypto"], 4)
    crypto_links = {x["link"] for x in by_cls["crypto"]}
    fx_pool = [x for x in all_news.values() if x["link"] not in crypto_links and _FX_NEWS.search(x["raw"])]
    fx_news = newest(fx_pool or [x for x in all_news.values() if x["tag"] == TAG_MACRO[0]], 4)

    # ---------------- Cảnh báo ----------------
    clock = now_vn.strftime("%H:%M")
    sev = {"KHẨN": (DN, "#fdeaef", 3), "CHÚ Ý": ("#b45309", "#fbf0df", 2), "TIN": (ACCENT, "rgba(216,30,44,.1)", 1)}
    alerts = []

    def alert(level, icon, icon_col, title, detail, time=clock):
        c, bg, rank = sev[level]
        alerts.append({"level": level, "sev_col": c, "sev_bg": bg, "rank": rank, "icon": icon,
                       "icon_col": icon_col, "title": title, "detail": detail, "time": time})

    # Cảnh báo của lệnh đang mở (nhật ký lệnh) — đặt lên đầu trong cùng mức độ.
    for al in (trade_alerts or [])[:5]:
        when = datetime.fromisoformat(al["ts"]).astimezone(_VN_TZ).strftime("%H:%M")
        alert(al["level"], "📌", ACCENT, f"Lệnh #{al['trade_id']} {'MUA' if al['side'] == 'buy' else 'BÁN'} "
              f"{al['symbol']}", al["text"], when)
    for g in ("VN30", "HNX30"):
        for sym in groups.get(g, []):
            s = q.get(f"VN:{sym}", {})
            a = abs(s.get("chg") or 0)
            if a >= 3.2:
                upm = s["chg"] > 0
                alert("KHẨN" if a >= 4.5 else "CHÚ Ý", "▲" if upm else "▼", UP if upm else DN,
                      f"{sym} {'tăng' if upm else 'giảm'} {s['chg']:+.2f}%",
                      f"{s.get('name', sym)} · giá {s['price'] / 1000:,.2f} · rổ {g}")
    if flow["has"] and abs(fnet) > 500e9:
        alert("KHẨN" if abs(fnet) > 2000e9 else "TIN", "⚡", UP if fnet >= 0 else DN,
              f"Khối ngoại {'mua' if fnet >= 0 else 'bán'} ròng mạnh", f"{_bil(fnet, True)} trên sàn HOSE")
    for s in sectors:
        if abs(s["chg"] or 0) >= 2.2:
            alert("CHÚ Ý", "◉", UP if s["chg"] > 0 else DN,
                  f"Ngành {s['name']} {'bứt phá' if s['chg'] > 0 else 'lao dốc'}",
                  f"Chỉ số {s['code']} {s['chg']:+.2f}%")
    for k, cls in _ASSET_CLASS.items():
        c = price(k)[1]
        thr = MOVE_ALERT_PCT.get(cls)
        if c is not None and thr and abs(c) >= thr:
            alert("KHẨN" if abs(c) >= 2 * thr else "CHÚ Ý", "▲" if c > 0 else "▼", UP if c > 0 else DN,
                  f"{_SHORT_NAMES.get(k, k)} biến động {c:+.2f}%", f"Vượt ngưỡng ±{thr}% trong phiên")
    for r in results:
        thr = ANOMALY_THRESHOLD_PCT.get(r["asset_class"], 10.0)
        name = _SHORT_NAMES.get(r["key"], r["name"])
        if r["status"] == "ERROR":
            alert("KHẨN", "⛔", DN, f"{name}: không nguồn nào trả dữ liệu", r["notes"])
        elif r["change_pct"] is not None and abs(r["change_pct"]) >= thr:
            alert("KHẨN", "⚡", DN, f"{name} biến động {r['change_pct']:+.2f}%",
                  f"Vượt ngưỡng bất thường ±{thr}% so với kỳ trước.")
        elif r["status"] == "WARN" and r["num_sources"] >= 2:
            alert("CHÚ Ý", "⚠", "#b45309", f"{name}: nguồn lệch {r['max_diff_pct']:.2f}%", r["notes"])
    for it in all_news.values():
        if it["impact"] == 3 and it["verified"]:
            alert("CHÚ Ý", "📰", "#b45309", it["title"][:90], f"Tin ảnh hưởng cao · {it['source']}", it["time"])
    alerts.sort(key=lambda a: -a["rank"])

    # ---------------- Lịch sự kiện trong tuần (cuối tuần -> tuần kế tiếp) ----------------
    monday = (now_vn + timedelta(days=7 if now_vn.weekday() >= 5 else 0)).date()
    monday -= timedelta(days=monday.weekday())
    imp_col = {3: DN, 2: "#b45309", 1: "#7d8f86"}
    events = market.get("calendar", [])
    week = []
    for i in range(5):
        day = monday + timedelta(days=i)
        evs = [e for e in events if e["dt"][:10] == day.isoformat() and e["imp"] >= 2]
        evs.sort(key=lambda e: (-e["imp"], e["dt"]))
        shown = sorted(evs[:3], key=lambda e: e["dt"])
        week.append({
            "day": _WEEKDAYS[i], "date": day.strftime("%d/%m"), "today": day == now_vn.date(),
            "more": len(evs) - len(shown),
            "events": [{"time": e["dt"][11:16], "title": e["vi"], "orig": e["title"], "country": e["country"],
                        "dot": imp_col[e["imp"]], "actual": e.get("actual"), "res": _ev_result(e),
                        "detail": " · ".join(x for x in (f"Thực tế {e['actual']}" if e.get("actual") else "",
                                                           f"Dự báo {e['forecast']}" if e["forecast"] else "",
                                                           f"Kỳ trước {e['previous']}" if e["previous"] else "") if x)}
                       for e in shown]})
    # Kết quả đã công bố (ảnh hưởng trung bình trở lên), mới nhất trước.
    results = [{"when": f"{_WEEKDAYS[datetime.fromisoformat(e['dt']).weekday()]} {e['dt'][11:16]}",
                "title": e["vi"], "orig": e["title"], "country": e["country"], "dot": imp_col[e["imp"]],
                "actual": e["actual"], "forecast": e["forecast"], "previous": e["previous"], **_ev_result(e)}
               for e in sorted(events, key=lambda e: e["dt"], reverse=True)
               if e.get("eval") and e["imp"] >= 2][:8]

    return {
        "accent": ACCENT, "vn_open": vn_open, "cards": cards, "ticker": ticker,
        "sectors": sectors, "mover_groups": mover_groups, "alerts": alerts[:6], "alert_count": len(alerts),
        "news_fields": news_fields, "week": week, "has_calendar": bool(events), "ev_results": results,
        "tone_effects": market_effects(_lean(tone)),
        "board": board, "breadth": breadth, "liquidity": liquidity, "flow": flow,
        "stock_news": stock_news, "commod_news": commod_news, "fx_news": fx_news,
        "btc": btc_hero, "crypto": crypto, "commod": commod, "fx": fx,
        "bank_ts": market.get("bank_ts"), "tone": tone,
        "generated": now.isoformat(timespec="seconds"),
    }
