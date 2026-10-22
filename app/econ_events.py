"""Kết quả sự kiện kinh tế trong tuần: số THỰC TẾ so với KỲ VỌNG và tác động thị trường.

- Lịch (ForexFactory) chỉ có dự báo & kỳ trước; số thực tế lấy từ lịch kinh tế Nasdaq.
- Ghép sự kiện theo quốc gia + giờ công bố (giờ New York) + số dự báo/kỳ trước trùng khớp
  (cùng tên chỉ báo thường có 2 dòng m/m và y/y nên không ghép theo tên).
- Đánh giá: đúng / cao hơn / thấp hơn kỳ vọng, mức lệch, ý nghĩa kinh tế, và tác động tới từng nhóm
  thị trường (dùng chung cơ chế Hawkish/Dovish của market_tone).
Mang tính tham khảo theo quy tắc phổ biến — không phải khuyến nghị đầu tư.
"""
import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from .market_tone import market_effects

_NY = ZoneInfo("America/New_York")
_NASDAQ = "https://api.nasdaq.com/api/calendar/economicevents"

# Mã tiền tệ (ForexFactory) -> tên quốc gia (Nasdaq)
_COUNTRIES = {
    "USD": {"United States"}, "GBP": {"United Kingdom"}, "JPY": {"Japan"}, "CAD": {"Canada"},
    "AUD": {"Australia"}, "NZD": {"New Zealand"}, "CHF": {"Switzerland"}, "CNY": {"China"},
    "EUR": {"Euro Zone", "Germany", "France", "Italy", "Spain"},
}
_CB = {"USD": "Fed", "EUR": "ECB", "GBP": "BoE", "JPY": "BoJ", "CAD": "BoC", "AUD": "RBA",
       "NZD": "RBNZ", "CHF": "SNB", "CNY": "PBoC"}
_MULT = {"": 1.0, "%": 1.0, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}
_NUM_RE = re.compile(r"^[<>]?\s*(-?[\d,]*\.?\d+)\s*([%KMBT]?)", re.I)


def key(e: dict) -> str:
    return f"{e['country']}|{e['dt']}|{e['title']}"


# ---------------------------------------------------------------------------
# Lấy số thực tế
# ---------------------------------------------------------------------------
def fetch_day(client, d: date) -> List[dict]:
    """Các dòng lịch Nasdaq của ngày d (giờ New York).

    API Nasdaq lệch một ngày: truy vấn ngày D trả về sự kiện ngày D-1 -> lấy D+1 và D để chắc chắn.
    """
    rows = []
    for q in (d + timedelta(days=1), d):
        r = client.get(_NASDAQ, params={"date": q.isoformat()},
                       headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        rows += ((r.json().get("data") or {}).get("rows") or [])
    return rows


def _same(a: str, b: str) -> bool:
    x, y = _val(a), _val(b)
    return x is not None and y is not None and abs(x[0] - y[0]) < 1e-9


def match(e: dict, rows: List[dict]) -> Optional[dict]:
    """Dòng Nasdaq ứng với sự kiện ForexFactory e (đã có số thực tế), hoặc None."""
    ny = datetime.fromisoformat(e["dt"]).astimezone(_NY)
    names = _COUNTRIES.get(e["country"], set())
    words = set(re.findall(r"[a-z]+", e["title"].lower())) - {"m", "y", "q", "w"}
    ref = _val(e.get("forecast") or "") or _val(e.get("previous") or "")
    best, best_score = None, 0
    for r in rows:
        if r.get("country") not in names or (r.get("gmt") or "").strip() != ny.strftime("%H:%M"):
            continue
        act = _val(r.get("actual") or "")
        if act is None or (ref and (act[1] == "%") != (ref[1] == "%")):
            continue  # khác đơn vị (vd chỉ số điểm vs %) -> không phải cùng chỉ báo
        score = 0
        if e.get("forecast") and _same(e["forecast"], r.get("consensus") or ""):
            score += 3
        if e.get("previous") and _same(e["previous"], r.get("previous") or ""):
            score += 3
        score += len(words & set(re.findall(r"[a-z]+", (r.get("eventName") or "").lower())))
        if score > best_score:
            best, best_score = r, score
    # Cần trùng ít nhất một con số (dự báo / kỳ trước) để tránh ghép nhầm chỉ báo khác cùng giờ.
    return best if best_score >= 3 else None


# ---------------------------------------------------------------------------
# Đánh giá so với kỳ vọng
# ---------------------------------------------------------------------------
def _val(s: str) -> Optional[Tuple[float, str]]:
    m = _NUM_RE.match((s or "").strip())
    if not m:
        return None
    unit = m.group(2).upper()
    return float(m.group(1).replace(",", "")) * _MULT[unit], unit


def _decimals(s: str) -> int:
    m = _NUM_RE.match((s or "").strip())
    num = m.group(1) if m else ""
    return len(num.split(".")[1]) if "." in num else 0


def _kind(title: str) -> Tuple[str, int]:
    """(nhóm chỉ báo, chiều): chiều +1 = số cao hơn là kinh tế mạnh hơn / chính sách chặt hơn."""
    t = title.lower()
    if "crude" in t or "oil stock" in t or "inventor" in t or "natural gas storage" in t:
        return "oil", -1
    if "unemployment" in t or "jobless" in t or "claimant" in t:
        return "labor", -1
    if "borrowing" in t or "deficit" in t:
        return "growth", -1
    if re.search(r"funds rate|bank rate|cash rate|refinancing|policy rate|overnight rate|loan prime|interest rate|deposit", t):
        return "rate", +1
    if re.search(r"cpi|ppi|pce|price|inflation|hicp|earnings|wage", t):
        return "inflation", +1
    if re.search(r"employment|payroll|jobs", t):
        return "labor", +1
    return "growth", +1


def _fmt_dev(dev: float, unit: str) -> str:
    if unit == "%":
        return f"{dev:+.2f}".rstrip("0").rstrip(".") + " điểm %"
    v = dev / _MULT[unit]
    return f"{v:+,.3f}".rstrip("0").rstrip(".") + unit


def evaluate(e: dict) -> Optional[dict]:
    """So sánh số thực tế với dự báo (thiếu dự báo -> so với kỳ trước) và ước tính tác động."""
    a = _val(e.get("actual") or "")
    ref_s, vs = (e.get("forecast"), "kỳ vọng") if _val(e.get("forecast") or "") else (e.get("previous"), "kỳ trước")
    ref = _val(ref_s or "")
    if a is None or ref is None:
        return None
    dev = a[0] - ref[0]
    tol = 0.5 * 10 ** -_decimals(ref_s) * _MULT[ref[1]]
    kind, pol = _kind(e["title"])
    ccy, cb = e["country"], _CB.get(e["country"], "NHTW")

    if abs(dev) <= tol + 1e-12:
        status, sign, strong = "inline", 0, False
        label = f"Đúng {vs}"
    else:
        sign = 1 if dev > 0 else -1
        status = "above" if sign > 0 else "below"
        label = f"{'Cao' if sign > 0 else 'Thấp'} hơn {vs}"
        base = abs(ref[0]) or abs(a[0]) or 1.0
        strong = abs(dev) >= 0.2 if ref[1] == "%" else abs(dev) / base >= 0.1
    econ = sign * pol  # +1: kinh tế mạnh hơn / áp lực thắt chặt (hawkish) — với "oil" là tồn kho

    # Ý nghĩa kinh tế
    if status == "inline":
        meaning = "Khớp dự báo — thị trường đã phản ánh trước, tác động thường hạn chế."
    elif kind == "oil":
        meaning = ("Tồn kho tăng nhiều hơn dự kiến → cung dư, áp lực giảm giá dầu." if sign > 0
                   else "Tồn kho thấp hơn dự kiến → cung thắt chặt, hỗ trợ giá dầu.")
    elif kind == "inflation":
        meaning = (f"Lạm phát nóng hơn dự kiến → {cb} khó nới lỏng, nghiêng về giữ lãi suất cao (Hawkish)." if sign > 0
                   else f"Lạm phát hạ nhiệt hơn dự kiến → mở dư địa cho {cb} nới lỏng (Dovish).")
    elif kind == "rate":
        meaning = (f"{cb} thắt chặt hơn kỳ vọng (Hawkish)." if sign > 0
                   else f"{cb} nới lỏng mạnh hơn kỳ vọng (Dovish).")
    elif kind == "labor":
        meaning = ("Thị trường lao động mạnh hơn dự kiến → giảm khả năng hạ lãi suất (Hawkish)." if econ > 0
                   else f"Thị trường lao động yếu hơn dự kiến → tăng khả năng {cb} hạ lãi suất (Dovish).")
    else:
        meaning = ("Kinh tế tốt hơn dự kiến → hỗ trợ đồng tiền, giảm nhu cầu nới lỏng." if econ > 0
                   else "Kinh tế yếu hơn dự kiến → đồng tiền chịu áp lực, tăng kỳ vọng nới lỏng.")
    if status != "inline" and vs == "kỳ trước":
        meaning = "Không có dự báo — so với kỳ trước. " + meaning

    # Tác động tới các nhóm thị trường
    # Sự kiện ảnh hưởng thấp chỉ tạo tác động đáng kể khi lệch mạnh (|lean| < 15 = trung tính).
    scale = (60 if strong else 35) * {3: 1.0, 2: 0.7}.get(e.get("imp", 1), 0.4)
    effects: List[dict] = []
    if status != "inline":
        if kind == "oil":
            d = -sign
            effects = [{"name": "Dầu", "dir": d, "label": "Tích cực" if d > 0 else "Tiêu cực",
                        "arrow": "▲" if d > 0 else "▼", "why": meaning}]
        elif ccy == "USD":
            effects = [x for x in market_effects(econ * scale) if x["dir"] != 0]
        else:
            effects = [{"name": ccy, "dir": econ, "label": "Tăng" if econ > 0 else "Giảm",
                        "arrow": "▲" if econ > 0 else "▼",
                        "why": f"{ccy} {'mạnh lên' if econ > 0 else 'suy yếu'} so với USD"}]
            if ccy == "CNY" and kind in ("growth", "labor"):
                for name, why in (("Dầu", "nhu cầu năng lượng của Trung Quốc"),
                                  ("CK Việt Nam", "đối tác thương mại lớn nhất của Việt Nam")):
                    effects.append({"name": name, "dir": econ, "label": "Tích cực" if econ > 0 else "Tiêu cực",
                                    "arrow": "▲" if econ > 0 else "▼", "why": why})
    tone = None if status == "inline" or kind == "oil" else ("hawkish" if econ > 0 else "dovish")
    return {
        "status": status, "label": label, "strong": strong,
        "strength": "" if status == "inline" else ("lệch mạnh" if strong else "lệch nhẹ"),
        "dev": "" if status == "inline" else _fmt_dev(dev, ref[1]), "vs": vs,
        "tone": tone, "meaning": meaning, "effects": effects,
    }


def telegram_text(e: dict) -> str:
    """Tin nhắn Telegram (HTML) cho một sự kiện vừa công bố kết quả."""
    import html
    ev = e["eval"]
    icon = {"inline": "⚪", "above": "🔺", "below": "🔻"}[ev["status"]]
    nums = " · ".join(x for x in (f"Thực tế <b>{html.escape(e['actual'])}</b>",
                                  f"Dự báo {html.escape(e['forecast'])}" if e.get("forecast") else "",
                                  f"Kỳ trước {html.escape(e['previous'])}" if e.get("previous") else "") if x)
    lines = [f"📅 <b>Kết quả: {html.escape(e['vi'])}</b> ({html.escape(e['country'])})", nums,
             f"{icon} <b>{ev['label']}</b>" + (f" ({ev['strength']}, {ev['dev']})" if ev["dev"] else ""),
             html.escape(ev["meaning"])]
    if ev["effects"]:
        lines.append("Tác động: " + " · ".join(f"{x['name']} {x['arrow']}" for x in ev["effects"]))
    lines.append("<i>Ước tính theo quy tắc phổ biến — tham khảo, không phải khuyến nghị đầu tư.</i>")
    return "\n".join(lines)
