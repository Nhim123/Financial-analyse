"""Đánh giá rủi ro một lệnh dựa trên bối cảnh cơ bản (fundamental) của thị trường.

Đầu vào: tài sản (cổ phiếu VN, vàng/dầu/hàng hoá, crypto), chiều Mua/Bán, giá vào, cắt lỗ, chốt lời,
khối lượng. Đầu ra: điểm rủi ro 0–100 (cao = rủi ro hơn) kèm từng yếu tố và lý do, cùng các kiểm tra
quản lý vốn theo cài đặt của người dùng (vốn, % rủi ro tối đa mỗi lệnh, tỷ trọng tối đa mỗi mã).

Yếu tố thị trường (điểm 50 = trung tính; lệch về phía bất lợi cho CHIỀU của lệnh thì tăng):
  1. Xu hướng thị trường chung   — VN: VN-Index, độ rộng, khối ngoại HOSE; crypto: BTC, S&P 500;
                                    hàng hoá: USD (EUR/USD), S&P 500.
  2. Chính sách tiền tệ          — chỉ số Dovish/Hawkish của dòng tin -> tác động tới nhóm thị trường.
  3. Tin tức liên quan           — tin nhắc tới mã/tài sản: hướng Tích cực/Tiêu cực × mức ảnh hưởng × độ mới.
  4. Sự kiện kinh tế sắp tới     — sự kiện tác động Cao trong 48 giờ (rủi ro biến động, không theo chiều).
  5. Biến động & vị thế giá      — đuổi giá sau phiên tăng/giảm mạnh, sát trần/sàn.
  6. Thanh khoản (cổ phiếu VN)   — giá trị khớp lệnh của mã, tỷ trọng lệnh so với thanh khoản phiên.
  7. Khối ngoại trên mã (VN)     — mua/bán ròng của nhà đầu tư nước ngoài so với thanh khoản.
  8. Độ tin cậy dữ liệu          — kết quả kiểm chứng đa nguồn của giá.
Đây là đánh giá định lượng để tham khảo, KHÔNG phải khuyến nghị mua/bán.
"""
import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from . import levels, live, marketdata, news_service, storage
from .config import CRYPTO, EXTRA_CRYPTO
from .market_tone import aggregate as tone_aggregate, market_effects

VN_TZ = timezone(timedelta(hours=7))
_DEFAULT_SETTINGS = {"capital": 0.0, "max_risk_pct": 2.0, "max_pos_pct": 20.0}
_CRYPTO_DEC = {"BTC": 0, "ETH": 1, "BNB": 1, "SOL": 2, "XRP": 4, "ADA": 4, "DOGE": 4, "AVAX": 2}
_CRYPTO_VOL = {"BTC": 3.0, "ETH": 4.0}

# Tài sản ngoài cổ phiếu VN. mult: giá hiển thị -> tiền gốc (ccy); vol: biến động ngày điển hình (%);
# market: nhóm thị trường trong market_tone.MARKETS; news: biểu thức nhận diện tin liên quan.
ASSETS: Dict[str, dict] = {
    "SJC": dict(name="Vàng miếng SJC", cls="commodity", unit="triệu ₫/lượng", qty_unit="lượng", mult=1e6, ccy="VND",
                market="vang", news=r"gold|vàng|sjc|bullion", dec=2, vol=1.0),
    "XAU": dict(name="Vàng thế giới", cls="commodity", unit="USD/oz", qty_unit="oz", mult=1, ccy="USD",
                market="vang", news=r"gold|vàng|bullion|xau", dec=1, vol=1.2),
    "XAG": dict(name="Bạc", cls="commodity", unit="USD/oz", qty_unit="oz", mult=1, ccy="USD",
                market="vang", news=r"silver|bạc", dec=2, vol=2.0),
    "WTI": dict(name="Dầu WTI", cls="commodity", unit="USD/thùng", qty_unit="thùng", mult=1, ccy="USD",
                market="dau", news=r"oil|crude|dầu|opec|wti", dec=2, vol=2.0),
    "BRENT": dict(name="Dầu Brent", cls="commodity", unit="USD/thùng", qty_unit="thùng", mult=1, ccy="USD",
                  market="dau", news=r"oil|crude|dầu|opec|brent", dec=2, vol=2.0),
    "NG": dict(name="Khí tự nhiên", cls="commodity", unit="USD/MMBtu", qty_unit="MMBtu", mult=1, ccy="USD",
               market="dau", news=r"natural gas|lng|khí đốt|khí tự nhiên", dec=3, vol=3.5),
    "HG": dict(name="Đồng", cls="commodity", unit="USD/lb", qty_unit="lb", mult=1, ccy="USD",
               market=None, news=r"copper|kim loại đồng", dec=3, vol=1.5),
    "KC": dict(name="Cà phê Arabica", cls="commodity", unit="US¢/lb", qty_unit="lb", mult=0.01, ccy="USD",
               market=None, news=r"coffee|cà phê", dec=2, vol=2.5),
}
for _c in CRYPTO + EXTRA_CRYPTO:
    ASSETS[_c["key"]] = dict(name=_c["name"], cls="crypto", unit="USD", qty_unit=_c["key"], mult=1, ccy="USD",
                             market="crypto", news=rf"{re.escape(_c['key'])}|{re.escape(_c['name'])}",
                             dec=_CRYPTO_DEC.get(_c["key"], 2), vol=_CRYPTO_VOL.get(_c["key"], 6.0))
_ALIASES = {"GOLD": "XAU", "VANG": "SJC", "VÀNG": "SJC", "SILVER": "XAG", "BAC": "XAG", "OIL": "WTI",
            "DAU": "WTI", "DẦU": "WTI", "BITCOIN": "BTC", "ETHEREUM": "ETH"}
# Tiền tệ có sự kiện kinh tế ảnh hưởng tới từng nhóm tài sản.
_EVENT_CCY = {"vn": {"USD", "CNY"}, "commodity": {"USD", "CNY"}, "crypto": {"USD"}}


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _f(x) -> Optional[float]:
    """Số từ chuỗi người dùng nhập: '58,2' -> 58.2; '81,000' / '1.250.000' -> phân tách hàng nghìn;
    rỗng -> None."""
    if x is None or (isinstance(x, str) and not x.strip()):
        return None
    s = str(x).replace(" ", "")
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", s):
        s = re.sub(r"[.,]", "", s)
    try:
        v = float(s.replace(",", "."))
        return v if math.isfinite(v) else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Cài đặt quản lý vốn (lưu trong bảng kv của database cục bộ — không đưa lên GitHub)
# ---------------------------------------------------------------------------
def get_settings() -> dict:
    try:
        s = json.loads(storage.kv_get("risk_settings") or "{}")
    except ValueError:
        s = {}
    return {**_DEFAULT_SETTINGS, **{k: v for k, v in s.items() if k in _DEFAULT_SETTINGS}}


def save_settings(data: dict) -> dict:
    s = get_settings()
    for k, lo, hi in (("capital", 0, 1e15), ("max_risk_pct", 0.1, 100), ("max_pos_pct", 1, 100)):
        raw = data.get(k)
        if isinstance(raw, (int, float)):
            v = float(raw)
        elif k == "capital" and raw is not None:  # "500.000.000" / "500,000,000" -> 500000000
            v = _f(re.sub(r"[.,\s]", "", str(raw)))
        else:
            v = _f(raw)
        if v is not None:
            s[k] = max(lo, min(hi, v))
    storage.kv_set("risk_settings", json.dumps(s))
    return s


# ---------------------------------------------------------------------------
# Tài sản
# ---------------------------------------------------------------------------
def resolve(symbol: str, quotes: Optional[dict] = None) -> Optional[str]:
    """'fpt' / 'VN:FPT' -> 'VN:FPT'; 'btc' -> 'BTC'; 'vàng' -> 'SJC'. None nếu không nhận ra."""
    q = quotes if quotes is not None else live.quotes()
    s = (symbol or "").strip().split(" ")[0].upper()
    s = _ALIASES.get(s, s)
    if s.startswith("VN:"):
        s = s[3:]
    if s in ASSETS:
        return s
    return f"VN:{s}" if f"VN:{s}" in q else None


def asset_info(key: str, q: dict) -> Optional[dict]:
    if key.startswith("VN:"):
        s = q.get(key)
        if not s:
            return None
        return dict(key=key, symbol=key[3:], name=s.get("name", key[3:]), cls="vn", unit="nghìn ₫",
                    qty_unit="cổ phiếu", mult=1000, ccy="VND", market="ck_vn",
                    news=re.escape(key[3:]), dec=2, vol=2.5, scale=0.001, lot=100)
    a = ASSETS.get(key)
    if not a:
        return None
    return dict(key=key, symbol=key, scale=1, lot=None, **a)


def asset_list() -> List[dict]:
    """Danh sách tài sản cho ô chọn trên giao diện (kèm thông tin để điền sẵn giá)."""
    q, groups = live.quotes(), live.groups()
    out = []
    for k, a in ASSETS.items():
        if k in q:
            out.append({"k": k, "s": k, "n": a["name"], "c": a["cls"], "u": a["unit"], "qu": a["qty_unit"],
                        "d": a["dec"], "sc": 1, "lot": 0})
    seen = set()
    for g in ("VN30", "HNX30", "HOSE"):
        for sym in groups.get(g, []):
            key = f"VN:{sym}"
            if key in seen or key not in q:
                continue
            seen.add(key)
            out.append({"k": key, "s": sym, "n": q[key].get("name", sym), "c": "vn", "u": "nghìn ₫",
                        "qu": "cp", "d": 2, "sc": 0.001, "lot": 100})
    return out


# ---------------------------------------------------------------------------
# Đánh giá
# ---------------------------------------------------------------------------
def usdvnd(q: dict) -> float:
    return (q.get("USDVND") or {}).get("price") or 25500.0


def context() -> dict:
    """Dữ liệu dùng chung cho một lượt đánh giá (gom một lần để đánh giá nhiều lệnh)."""
    run_id = storage.latest_run()
    news = news_service.for_run(run_id) if run_id else {}
    items: Dict[str, dict] = {}
    for block in news.values():
        for n in block["items"]:
            items.setdefault(n["link"], n)
    return {"q": live.quotes(), "groups": live.groups(), "market": marketdata.snapshot(),
            "news": news, "news_items": list(items.values()),
            "results": {r["key"]: r for r in (storage.get_run(run_id) if run_id else [])},
            "now": datetime.now(timezone.utc)}


def _lean(agg: Optional[dict]) -> Optional[float]:
    if not agg or agg.get("net") is None:
        return None
    return agg["net"] * min(1.0, agg.get("n_signal_items", 0) / 5)


def _factor(name: str, score: float, weight: float, detail: str, na: bool = False) -> dict:
    score = round(_clamp(score, 0, 100))
    kind = "na" if na else ("risk" if score >= 60 else ("ok" if score <= 40 else "neutral"))
    return {"name": name, "score": score, "weight": weight, "detail": detail, "kind": kind}


def _market_trend(a: dict, s: int, ctx: dict) -> dict:
    q = ctx["q"]

    def chg(k):
        return (q.get(k) or {}).get("chg") or 0.0

    if a["cls"] == "vn":
        vni = q.get("VNINDEX", {})
        adv, dec = vni.get("adv") or 0, vni.get("dec") or 0
        b = (adv - dec) / (adv + dec) if adv + dec else 0.0
        hose = [q.get(f"VN:{x}", {}) for x in ctx["groups"].get("HOSE", [])]
        fnet = sum((h.get("fbv") or 0) - (h.get("fsv") or 0) for h in hose)
        senti = _clamp(chg("VNINDEX") / 1.5) * 0.5 + _clamp(b / 0.4) * 0.3 + _clamp(fnet / 1000e9) * 0.2
        detail = (f"VN-Index {chg('VNINDEX'):+.2f}%, {adv} mã tăng / {dec} mã giảm, "
                  f"khối ngoại HOSE {fnet / 1e9:+,.0f} tỷ")
    elif a["cls"] == "crypto":
        senti = _clamp(chg("BTC") / 4) * 0.6 + _clamp(chg("SPX") / 1.5) * 0.4
        detail = f"BTC {chg('BTC'):+.2f}% (24h), S&P 500 {chg('SPX'):+.2f}% — khẩu vị rủi ro chung"
    else:
        usd_weak = chg("EURUSD")  # EUR/USD tăng = USD yếu đi
        if a["market"] == "vang":
            senti = _clamp(usd_weak / 0.5) * 0.7 + _clamp(-chg("SPX") / 1.5) * 0.3
            usd = "đi ngang" if abs(usd_weak) < 0.05 else ("yếu đi" if usd_weak > 0 else "mạnh lên")
            detail = (f"EUR/USD {usd_weak:+.2f}% (USD {usd}), S&P 500 {chg('SPX'):+.2f}% "
                      f"(vàng là kênh trú ẩn khi cổ phiếu giảm)")
        else:
            senti = _clamp(chg("SPX") / 1.5) * 0.6 + _clamp(usd_weak / 0.5) * 0.4
            detail = f"S&P 500 {chg('SPX'):+.2f}% (kỳ vọng tăng trưởng/nhu cầu), EUR/USD {usd_weak:+.2f}%"
    side_fit = s * senti
    return _factor("Xu hướng thị trường chung", 50 - 45 * side_fit, 20,
                   detail + (" → thuận chiều lệnh" if side_fit > 0.15 else
                             (" → ngược chiều lệnh" if side_fit < -0.15 else " → trung tính")))


def _policy(a: dict, s: int, ctx: dict) -> dict:
    name = "Chính sách tiền tệ (Dovish/Hawkish)"
    if not a["market"]:
        return _factor(name, 50, 20, f"{a['name']} ít nhạy với lãi suất — không tính yếu tố này", na=True)
    all_items = ctx["news_items"]
    lean = _lean(tone_aggregate(all_items)) if all_items else None
    if a["cls"] == "vn":  # thêm tín hiệu điều hành của NHNN trong tin Việt Nam
        vn_items = ctx["news"].get("vn", {}).get("items", [])
        vlean = _lean(tone_aggregate(vn_items)) if vn_items else None
        if vlean is not None:
            lean = vlean if lean is None else (lean + vlean) / 2
    eff = next(e for e in market_effects(lean) if e["key"] == a["market"])
    if lean is None or eff["dir"] == 0:
        return _factor(name, 50, 20, "Dòng tin chưa có tín hiệu chính sách rõ ràng (cân bằng/trung lập)")
    strength = min(1.0, abs(lean) / 60)
    stance = "HAWKISH (thắt chặt)" if lean > 0 else "DOVISH (nới lỏng)"
    return _factor(name, 50 - 40 * s * eff["dir"] * strength, 20,
                   f"Dòng tin thiên {stance}, điểm {lean:+.0f} → {eff['name']}: {eff['label'].lower()} "
                   f"({eff['why'].lower()})")


def _related_news(a: dict, ctx: dict) -> List[dict]:
    rx = re.compile(rf"\b(?:{a['news']})\b", re.I)
    out = [n for n in ctx["news_items"]
           if rx.search(f"{n['title']} {n.get('summary', '')} {n.get('vi_title', '')}")]
    return sorted(out, key=lambda n: n["age_hours"] if n["age_hours"] is not None else 999)


def _news(a: dict, s: int, related: List[dict]) -> dict:
    if not related:
        return _factor("Tin tức liên quan", 50, 20, f"Không có tin riêng về {a['symbol']} trong dòng tin hiện tại")
    total, pos, neg = 0.0, 0, 0
    for n in related:
        d = {"Tích cực": 1, "Tiêu cực": -1}.get(n["impact"].get("direction"), 0)
        age = n["age_hours"] if n["age_hours"] is not None else 99
        fresh = 1.0 if age < 6 else (0.6 if age < 24 else 0.3)
        total += d * n["impact"]["level"] * fresh
        pos += d > 0
        neg += d < 0
    return _factor("Tin tức liên quan", 50 - 40 * s * _clamp(total / 4), 20,
                   f"{len(related)} tin nhắc tới {a['symbol']}: {pos} tích cực, {neg} tiêu cực "
                   f"(trọng số theo mức ảnh hưởng và độ mới)")


def _events(a: dict, ctx: dict) -> Tuple[dict, List[dict]]:
    now = ctx["now"]
    ccys = _EVENT_CCY.get(a["cls"], {"USD"})
    upcoming = []
    for e in ctx["market"].get("calendar", []):
        hours = (datetime.fromisoformat(e["dt"]) - now).total_seconds() / 3600
        if 0 <= hours <= 48 and e["imp"] >= 2 and e["country"] in ccys:
            upcoming.append({**e, "hours": hours})
    hi24 = [e for e in upcoming if e["imp"] == 3 and e["hours"] <= 24]
    hi48 = [e for e in upcoming if e["imp"] == 3 and e["hours"] > 24]
    mid = [e for e in upcoming if e["imp"] == 2]
    score = 25 + 22 * len(hi24) + 10 * len(hi48) + 3 * len(mid)
    if not upcoming:
        detail = f"Không có sự kiện quan trọng ({'/'.join(sorted(ccys))}) trong 48 giờ tới"
    else:
        top = sorted(upcoming, key=lambda e: (-e["imp"], e["hours"]))[:2]
        detail = (f"{len(hi24)} sự kiện tác động cao trong 24h, {len(hi48)} trong 24–48h — "
                  + "; ".join(f"{datetime.fromisoformat(e['dt']).strftime('%d/%m %H:%M')} {e['country']} {e['vi']}"
                              for e in top))
    return (_factor("Sự kiện kinh tế sắp tới", min(score, 90), 15, detail),
            sorted(upcoming, key=lambda e: e["hours"])[:5])


def _volatility(a: dict, s: int, q: dict, entry: float) -> dict:
    c = q.get("chg") or 0.0
    vol = a["vol"]
    chase = s * c / vol
    score = 40 + _clamp(chase, -1, 2) * 18 + _clamp(abs(c) / vol - 1, 0, 2) * 10
    notes = [f"{'24h' if a['cls'] == 'crypto' else 'Trong phiên'} {c:+.2f}% (biến động ngày điển hình ~{vol:g}%)"]
    if chase > 1:
        notes.append("vào lệnh theo hướng giá đã chạy mạnh (đuổi giá)")
    elif chase < -1:
        notes.append("đi ngược nhịp biến động mạnh của phiên (bắt đáy/đỉnh)")
    if a["cls"] == "vn" and q.get("ceil") and q.get("floor"):
        p = entry / a["scale"]
        if s > 0 and (q["ceil"] - p) / p < 0.015:
            score += 15
            notes.append("giá sát trần — dễ đảo chiều, khó khớp")
        if p <= q["floor"] * 1.005:
            score += 15
            notes.append("giá đang ở vùng sàn")
    if not q.get("live"):
        notes.append("thị trường đang nghỉ — giá phiên gần nhất")
    return _factor("Biến động & vị thế giá", score, 15, "; ".join(notes))


def _liquidity(q: dict, pos_vnd: Optional[float]) -> dict:
    val = q.get("val") or 0
    if val <= 0:
        return _factor("Thanh khoản", 50, 10, "Chưa có giá trị khớp lệnh trong phiên", na=True)
    score = 80 if val < 5e9 else (60 if val < 20e9 else (40 if val < 100e9 else 22))
    detail = f"Giá trị khớp {val / 1e9:,.1f} tỷ/phiên"
    if pos_vnd and pos_vnd / val > 0.05:
        score += 15
        detail += f"; lệnh bằng {pos_vnd / val * 100:.1f}% thanh khoản phiên — khó vào/ra"
    return _factor("Thanh khoản", score, 10, detail)


def _foreign(s: int, q: dict) -> dict:
    val = q.get("val") or 0
    if val <= 0 or (q.get("fbv") is None and q.get("fsv") is None):
        return _factor("Khối ngoại trên mã", 50, 10, "Chưa có dữ liệu giao dịch khối ngoại", na=True)
    net = (q.get("fbv") or 0) - (q.get("fsv") or 0)
    ratio = net / val
    return _factor("Khối ngoại trên mã", 50 - 35 * s * _clamp(ratio / 0.1), 10,
                   f"Khối ngoại {'mua' if net >= 0 else 'bán'} ròng {abs(net) / 1e9:,.1f} tỷ "
                   f"({ratio * 100:+.1f}% thanh khoản mã)")


def _data_quality(a: dict, ctx: dict) -> dict:
    r = ctx["results"].get(a["key"])
    if not r:
        src = "SSI" if a["cls"] == "vn" else ("PNJ" if a["key"] == "SJC" else "CNBC/Binance")
        return _factor("Độ tin cậy dữ liệu", 40, 5, f"Giá từ một nguồn ({src}), không đối chiếu chéo")
    score = {"OK": 15, "WARN": 55, "ERROR": 85}.get(r["status"], 40)
    return _factor("Độ tin cậy dữ liệu", score, 5,
                   f"Kiểm chứng {r['num_sources']} nguồn: {r['status']}"
                   + (f" (lệch {r['max_diff_pct']:.2f}%)" if r.get("max_diff_pct") else ""))


def _money(a: dict, entry: float, stop: Optional[float], target: Optional[float], qty: Optional[float],
           settings: dict, q_all: dict, open_same: int, side: int = 1) -> dict:
    to_vnd = a["mult"] * (usdvnd(q_all) if a["ccy"] == "USD" else 1.0)  # 1 đơn vị giá hiển thị -> VND
    capital = settings["capital"] or 0
    checks = []
    rr = risk_amt = risk_pct = pos_vnd = pos_pct = max_qty = None
    stop_pct = abs(entry - stop) / entry * 100 if stop else None
    locked = bool(stop) and side * (entry - stop) <= 0  # vị thế đang mở: cắt lỗ đã dời lên vùng lãi

    if not stop:
        checks.append(("bad", "Chưa đặt cắt lỗ", "Lệnh không có điểm cắt lỗ — rủi ro không giới hạn"))
    elif locked:
        risk_amt = 0.0 if qty else None
        checks.append(("ok", "Cắt lỗ đã khoá lãi", f"Cắt lỗ cách giá vào {stop_pct:.2f}% về phía có lãi — "
                                                    "không còn rủi ro lỗ vốn"))
    else:
        per_unit = side * (entry - stop) * to_vnd
        if capital > 0 and per_unit > 0:
            max_qty = capital * settings["max_risk_pct"] / 100 / per_unit
            if a.get("lot"):
                max_qty = math.floor(max_qty / a["lot"]) * a["lot"]
        if qty:
            risk_amt = per_unit * qty
        if stop_pct < 0.5 * a["vol"]:
            checks.append(("warn", "Cắt lỗ quá sát",
                           f"Khoảng cắt lỗ {stop_pct:.2f}% < một nửa biến động ngày (~{a['vol']:g}%) — dễ bị quét"))
        else:
            checks.append(("ok", "Có cắt lỗ", f"Khoảng cắt lỗ {stop_pct:.2f}% so với giá vào"))
    if stop and target and not locked:
        rr = abs(target - entry) / abs(entry - stop)
        st = "ok" if rr >= 2 else ("warn" if rr >= 1 else "bad")
        checks.append((st, f"Tỷ lệ lời/lỗ (R:R) 1:{rr:.2f}",
                       "≥ 1:2 là hợp lý" if st == "ok" else "Lợi nhuận kỳ vọng thấp so với rủi ro chấp nhận"))
    elif not target and not locked:
        checks.append(("warn", "Chưa đặt chốt lời", "Không tính được tỷ lệ lời/lỗ"))
    if qty:
        pos_vnd = entry * to_vnd * qty
    if capital <= 0:
        checks.append(("warn", "Chưa nhập vốn", "Nhập quy mô vốn trong phần Cài đặt để kiểm tra % rủi ro và tỷ trọng"))
    elif qty:
        if risk_amt is not None:
            risk_pct = risk_amt / capital * 100
            lim = settings["max_risk_pct"]
            st = "ok" if risk_pct <= lim else ("warn" if risk_pct <= lim * 1.5 else "bad")
            checks.append((st, f"Rủi ro {risk_pct:.2f}% vốn nếu chạm cắt lỗ",
                           f"Giới hạn {lim:g}%/lệnh" + (f" · tối đa ~{max_qty:,.0f} {a['qty_unit']}"
                                                        if max_qty is not None else "")))
        pos_pct = pos_vnd / capital * 100
        lim = settings["max_pos_pct"]
        st = "ok" if pos_pct <= lim else ("warn" if pos_pct <= lim * 1.5 else "bad")
        checks.append((st, f"Tỷ trọng {pos_pct:.1f}% vốn", f"Giới hạn {lim:g}%/mã"))
    else:
        checks.append(("warn", "Chưa nhập khối lượng",
                       f"Theo cài đặt, khối lượng tối đa ~{max_qty:,.0f} {a['qty_unit']}" if max_qty
                       else "Nhập khối lượng để tính số tiền rủi ro"))
    if qty and a.get("lot") and qty % a["lot"]:
        checks.append(("warn", "Không phải lô chẵn", f"Sàn HOSE giao dịch lô chẵn {a['lot']} cổ phiếu"))
    if open_same:
        checks.append(("warn", f"Đang có {open_same} lệnh mở cùng mã", "Cộng dồn rủi ro trên cùng một tài sản"))
    return {"rr": rr, "risk_amt": risk_amt, "risk_pct": risk_pct, "pos_vnd": pos_vnd, "pos_pct": pos_pct,
            "max_qty": max_qty, "stop_pct": stop_pct, "to_vnd": to_vnd,
            "checks": [{"status": st, "label": lb, "detail": dt} for st, lb, dt in checks]}


def level_of(score: float) -> Tuple[str, str]:
    return ("Thấp", "lo") if score < 35 else (("Trung bình", "mid") if score < 60 else ("Cao", "hi"))


def evaluate(req: dict, ctx: Optional[dict] = None, open_same: int = 0) -> dict:
    """Đánh giá một lệnh. req: symbol|key, side ('buy'|'sell'), entry, stop, target, qty.
    req['position']=True: vị thế ĐANG MỞ (cắt lỗ/chốt lời so với giá hiện tại; kèm gợi ý dời cắt lỗ)."""
    ctx = ctx or context()
    q_all = ctx["q"]
    key = req.get("key") or resolve(req.get("symbol", ""), q_all)
    a = asset_info(key, q_all) if key else None
    if not a:
        return {"ok": False, "errors": [f"Không tìm thấy mã '{req.get('symbol') or req.get('key') or ''}' "
                                        "(cổ phiếu VN30/HNX30/HOSE, SJC, XAU, XAG, WTI, BRENT, NG, HG, KC, crypto)"]}
    q = q_all.get(key) or {}
    if not q.get("price"):
        return {"ok": False, "errors": [f"Chưa có giá trực tiếp cho {a['symbol']}"]}
    price = q["price"] * a["scale"]
    side = -1 if str(req.get("side", "buy")).lower() in ("sell", "ban", "bán", "short") else 1
    errors: List[str] = []
    if a["cls"] == "vn" and side < 0:
        errors.append("Thị trường cơ sở Việt Nam không cho bán khống — chỉ đánh giá lệnh MUA cổ phiếu")
    entry = _f(req.get("entry")) or price
    stop, target, qty = _f(req.get("stop")), _f(req.get("target")), _f(req.get("qty"))
    position = bool(req.get("position"))
    # Lệnh mới: so với giá vào. Vị thế đang mở: cắt lỗ có thể đã dời lên vùng lãi -> so với giá hiện tại;
    # cắt lỗ đã bị chạm / chốt lời đã vượt vẫn đánh giá được (trades.monitor sẽ cảnh báo riêng).
    ref, ref_name = entry, "giá vào"
    if entry <= 0:
        errors.append("Giá vào phải lớn hơn 0")
    if not position:
        if stop is not None and (stop <= 0 or (side > 0 and stop >= ref) or (side < 0 and stop <= ref)):
            errors.append(f"Cắt lỗ phải {'thấp' if side > 0 else 'cao'} hơn {ref_name} với lệnh "
                          f"{'MUA' if side > 0 else 'BÁN'}")
        if target is not None and (target <= 0 or (side > 0 and target <= ref) or (side < 0 and target >= ref)):
            errors.append(f"Chốt lời phải {'cao' if side > 0 else 'thấp'} hơn {ref_name} với lệnh "
                          f"{'MUA' if side > 0 else 'BÁN'}")
    elif (stop is not None and stop <= 0) or (target is not None and target <= 0):
        errors.append("Cắt lỗ / chốt lời phải lớn hơn 0")
    if qty is not None and qty <= 0:
        errors.append("Khối lượng phải lớn hơn 0")
    if a["cls"] == "vn" and q.get("ceil") and q.get("floor") and entry > 0 and not position:
        if not (q["floor"] * a["scale"] - 1e-9 <= entry <= q["ceil"] * a["scale"] + 1e-9):
            errors.append(f"Giá vào ngoài biên độ phiên: sàn {q['floor'] / 1000:,.2f} – trần {q['ceil'] / 1000:,.2f}")
    if errors:
        return {"ok": False, "errors": errors}

    settings = get_settings()
    money = _money(a, entry, stop, target, qty, settings, q_all, open_same, side)
    related = _related_news(a, ctx)
    ev_factor, events = _events(a, ctx)
    factors = [_market_trend(a, side, ctx), _policy(a, side, ctx), _news(a, side, related), ev_factor,
               _volatility(a, side, q, entry)]
    if a["cls"] == "vn":
        factors += [_liquidity(q, money["pos_vnd"]), _foreign(side, q)]
    factors.append(_data_quality(a, ctx))
    used = [f for f in factors if f["kind"] != "na"]
    market_score = sum(f["score"] * f["weight"] for f in used) / sum(f["weight"] for f in used)
    n_bad = sum(c["status"] == "bad" for c in money["checks"])
    n_warn = sum(c["status"] == "warn" for c in money["checks"])
    total = min(100.0, market_score + 8 * n_bad + 3 * n_warn)
    lvl, lvl_cls = level_of(total)
    mlvl, mcls = level_of(market_score)
    worst = sorted((f for f in used if f["kind"] == "risk"), key=lambda f: -f["score"] * f["weight"])
    try:  # gợi ý cắt lỗ / chốt lời tham chiếu (ATR, hỗ trợ/kháng cự, bội số R) — lỗi mạng thì bỏ qua
        lv = levels.suggest(a, "buy" if side > 0 else "sell", entry, qty=qty, to_vnd=money["to_vnd"],
                            settings=settings, price_now=price if position else None,
                            stop_current=stop if position else None, target_current=target if position else None,
                            opened_at=req.get("opened_at"), risk_score=market_score)
    except Exception:  # noqa: BLE001
        lv = None
    return {
        "ok": True, "key": key, "symbol": a["symbol"], "name": a["name"], "cls": a["cls"],
        "unit": a["unit"], "qty_unit": a["qty_unit"], "dec": a["dec"], "scale": a["scale"],
        "side": "buy" if side > 0 else "sell", "side_label": "MUA" if side > 0 else "BÁN",
        "price": price, "price_live": bool(q.get("live")), "entry": entry, "stop": stop, "target": target, "qty": qty,
        "score": round(total), "level": lvl, "level_class": lvl_cls,
        "market_score": round(market_score), "market_level": mlvl, "market_class": mcls,
        "money_status": "bad" if n_bad else ("warn" if n_warn else "ok"),
        "factors": factors, "money": money,
        "top_risks": [f["name"] for f in worst[:3]],
        "news": [{"title": n["vi_title"], "link": n["link"], "source": n["source_label"],
                  "age": n["age_hours"], "direction": n["impact"].get("direction"),
                  "impact": n["impact"].get("level_label")} for n in related[:5]],
        "events": [{"dt": e["dt"], "title": e["vi"], "country": e["country"], "imp": e["imp"]} for e in events],
        "settings": settings, "levels": lv, "position": position,
        "ts": ctx["now"].isoformat(timespec="seconds"),
        "disclaimer": "Đánh giá định lượng từ dữ liệu thị trường để tham khảo, không phải khuyến nghị mua/bán.",
    }
