"""Gợi ý mức cắt lỗ / chốt lời THAM CHIẾU theo các phương pháp kỹ thuật phổ biến.

Không phải khuyến nghị đầu tư — chỉ là phép tính trên dữ liệu giá để người dùng tự cân nhắc.

Dữ liệu: nến ngày (OHLC) — cổ phiếu VN: SSI (giá nghìn đồng); crypto: Binance; vàng/dầu/hàng hoá:
Yahoo Finance (hợp đồng tương lai); vàng SJC không có nến -> dùng biến động % của vàng thế giới.

Phương pháp:
- ATR(14) (Average True Range, kiểu Wilder): biên độ dao động trung bình một phiên.
- Hỗ trợ / kháng cự: đáy / đỉnh "fractal" (thấp/cao nhất trong 2 phiên mỗi bên) trong 60 phiên gần nhất.
- Lệnh MỚI:
    cắt lỗ = dưới hỗ trợ gần nhất 0,25×ATR nếu hỗ trợ cách giá vào 0,5–3×ATR; nếu không = 1,5×ATR;
    chốt lời = bội số rủi ro 2R (R = khoảng cắt lỗ); nếu kháng cự gần nằm trong 1,5–3R thì gợi ý chốt
    ngay trước kháng cự; kiểm tra ngân sách rủi ro theo cài đặt vốn.
- Vị thế ĐANG MỞ (theo giá hiện tại):
    lãi ≥ 1R -> dời cắt lỗ về hoà vốn; lãi ≥ 2R -> cắt lỗ theo xu hướng "Chandelier" = đỉnh cao nhất kể
    từ khi vào lệnh − 3×ATR (2×ATR khi rủi ro thị trường Cao); cắt lỗ chỉ dời theo hướng có lợi;
    giá đã vượt chốt lời -> gợi ý mục tiêu kế tiếp (kháng cự tiếp theo / 3R).
Lệnh BÁN (short) đối xứng. Làm tròn theo bước giá về phía an toàn (cắt lỗ xa hơn, chốt lời gần hơn).
"""
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from .collectors.http import get_client
from .config import CRYPTO, EXTRA_CRYPTO

VN_TZ = timezone(timedelta(hours=7))
_SSI_CHART = "https://iboard-api.ssi.com.vn/statistics/charts/history"
_SSI_HEADERS = {"Origin": "https://iboard.ssi.com.vn", "Referer": "https://iboard.ssi.com.vn/"}
_YAHOO = {"XAU": "GC=F", "WTI": "CL=F", "BRENT": "BZ=F", "XAG": "SI=F", "HG": "HG=F", "NG": "NG=F", "KC": "KC=F"}
_BINANCE = {c["key"]: c["binance"] for c in CRYPTO + EXTRA_CRYPTO}
_TTL_S = 1800
_cache: Dict[str, tuple] = {}
_lock = threading.Lock()

DISCLAIMER = ("Mức tham chiếu tính theo phương pháp kỹ thuật (ATR, hỗ trợ/kháng cự, bội số R) — "
              "không phải khuyến nghị đầu tư; bạn tự quyết định.")


# ---------------------------------------------------------------------------
# Nến ngày
# ---------------------------------------------------------------------------
def _fetch(key: str) -> List[dict]:
    with get_client() as c:
        if key.startswith("VN:"):
            now = int(time.time())
            r = c.get(f"{_SSI_CHART}?resolution=1D&symbol={key[3:]}&from={now - 150 * 86400}&to={now}",
                      headers=_SSI_HEADERS)
            r.raise_for_status()
            d = r.json().get("data") or {}
            return [{"t": t, "o": o, "h": h, "l": lo, "c": cl} for t, o, h, lo, cl in
                    zip(d.get("t", []), d.get("o", []), d.get("h", []), d.get("l", []), d.get("c", []))]
        if key in _BINANCE:
            r = c.get(f"https://api.binance.com/api/v3/klines?symbol={_BINANCE[key]}&interval=1d&limit=120")
            r.raise_for_status()
            return [{"t": k[0] // 1000, "o": float(k[1]), "h": float(k[2]), "l": float(k[3]), "c": float(k[4])}
                    for k in r.json()]
        if key in _YAHOO:
            r = c.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{_YAHOO[key]}?range=6mo&interval=1d")
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            return [{"t": t, "o": o, "h": h, "l": lo, "c": cl}
                    for t, o, h, lo, cl in zip(res["timestamp"], q["open"], q["high"], q["low"], q["close"])
                    if None not in (o, h, lo, cl)]
    return []


def bars(key: str) -> List[dict]:
    """Nến ngày (cũ -> mới), cache 30 phút. Lỗi mạng -> dùng bản cũ nếu có."""
    with _lock:
        hit = _cache.get(key)
    if hit and time.time() - hit[0] < _TTL_S:
        return hit[1]
    try:
        data = _fetch(key)
    except Exception:  # noqa: BLE001
        return hit[1] if hit else []
    with _lock:
        _cache[key] = (time.time(), data)
    return data


def atr(b: List[dict], n: int = 14) -> Optional[float]:
    if len(b) < n + 1:
        return None
    trs = [max(x["h"] - x["l"], abs(x["h"] - p["c"]), abs(x["l"] - p["c"])) for p, x in zip(b, b[1:])]
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def swings(b: List[dict], lookback: int = 60, k: int = 2) -> Dict[str, List[float]]:
    """Đáy/đỉnh fractal trong `lookback` phiên gần nhất."""
    b = b[-lookback:]
    lows, highs = [], []
    for i in range(k, len(b) - k):
        win = b[i - k:i + k + 1]
        if b[i]["l"] == min(x["l"] for x in win):
            lows.append(b[i]["l"])
        if b[i]["h"] == max(x["h"] for x in win):
            highs.append(b[i]["h"])
    return {"supports": lows, "resistances": highs}


def tick_round(key: str, v: float, dec: int, down: bool) -> float:
    """Làm tròn theo bước giá: HOSE (nghìn đồng) <10: 0,01 · 10–<50: 0,05 · ≥50: 0,1; tài sản khác theo
    số lẻ hiển thị. down=True làm tròn xuống, False làm tròn lên."""
    step = (0.01 if v < 10 else (0.05 if v < 50 else 0.1)) if key.startswith("VN:") else 10 ** -dec
    q = v / step
    return round((math.floor(q + 1e-9) if down else math.ceil(q - 1e-9)) * step, 6)


# ---------------------------------------------------------------------------
# Gợi ý
# ---------------------------------------------------------------------------
def suggest(a: dict, side: str, entry: float, *, qty: Optional[float] = None, to_vnd: float = 1.0,
            settings: Optional[dict] = None, price_now: Optional[float] = None,
            stop_current: Optional[float] = None, target_current: Optional[float] = None,
            opened_at: Optional[str] = None, risk_score: Optional[float] = None) -> dict:
    """a = risk.asset_info(...). Giá theo đơn vị hiển thị (cổ phiếu VN: nghìn đồng)."""
    key, dec = a["key"], a["dec"]
    s = 1 if side == "buy" else -1
    notes: List[str] = []

    def rnd(v: float) -> float:
        # MUA: làm tròn xuống (cắt lỗ xa hơn, chốt lời gần hơn); BÁN: làm tròn lên — luôn về phía an toàn.
        return tick_round(key, v, dec, s > 0)

    b = bars("XAU" if key == "SJC" else key)
    A = atr(b) if b else None
    sw = swings(b) if (b and key != "SJC") else {"supports": [], "resistances": []}
    if A and key == "SJC":
        A = A / b[-1]["c"] * entry
        notes.append("Vàng SJC không có dữ liệu nến — ATR suy ra từ biến động % của vàng thế giới.")
    if not A:
        A = entry * a.get("vol", 2.0) / 100
        notes.append(f"Thiếu dữ liệu nến — dùng biến động ngày điển hình ~{a.get('vol', 2.0):g}% thay cho ATR.")

    below = sorted((x for x in sw["supports"] if x < entry), reverse=True)
    above = sorted(x for x in sw["resistances"] if x > entry)
    protect = below if s > 0 else above   # vùng bảo vệ phía cắt lỗ
    obstacle = above if s > 0 else below  # vùng cản phía chốt lời

    # --- Cắt lỗ cho lệnh mới ---
    vol_stop = entry - s * 1.5 * A
    struct = next((x for x in protect if 0.5 * A <= abs(entry - x) <= 3 * A), None)
    if struct is not None:
        stop = struct - s * 0.25 * A
        method = f"{'dưới hỗ trợ' if s > 0 else 'trên kháng cự'} {struct:,.{dec}f} một khoảng 0,25×ATR"
    else:
        stop = vol_stop
        method = "cách giá vào 1,5×ATR (không có hỗ trợ/kháng cự phù hợp trong khoảng 0,5–3×ATR)"
    stop = rnd(stop)
    R = abs(entry - stop)

    # --- Chốt lời ---
    targets = [{"label": f"{m:g}R", "r": m, "value": rnd(entry + s * m * R)} for m in (1.5, 2, 3)]
    target, t_method = targets[1]["value"], "bội số 2R (lợi nhuận kỳ vọng gấp 2 lần rủi ro)"
    near = next((x for x in obstacle if abs(x - entry) > 0.3 * R), None)
    if near is not None and R:
        dist_r = abs(near - entry) / R
        if 1.5 <= dist_r <= 3:
            target = rnd(near - s * 0.1 * A)
            t_method = f"ngay trước {'kháng cự' if s > 0 else 'hỗ trợ'} {near:,.{dec}f} (≈{dist_r:.1f}R)"
        elif dist_r < 1.5:
            notes.append(f"{'Kháng cự' if s > 0 else 'Hỗ trợ'} gần {near:,.{dec}f} chỉ cách ≈{dist_r:.1f}R — giá có "
                         f"thể chững lại trước mục tiêu, tỷ lệ lời/lỗ kém hấp dẫn.")

    # --- Ngân sách rủi ro theo cài đặt vốn ---
    budget = None
    settings = settings or {}
    cap, lim = settings.get("capital") or 0, settings.get("max_risk_pct") or 0
    if cap > 0 and lim > 0 and R > 0:
        max_loss = cap * lim / 100
        qty_max = max_loss / (R * to_vnd)
        if a.get("lot"):
            qty_max = (qty_max // a["lot"]) * a["lot"]
        budget = {"max_loss": max_loss, "qty_max": qty_max}
        if qty and qty * R * to_vnd > max_loss:
            notes.append(f"Với cắt lỗ này, KL {qty:,g} rủi ro {qty * R * to_vnd:,.0f} ₫ — vượt giới hạn {lim:g}% vốn "
                         f"({max_loss:,.0f} ₫); KL tối đa ~{qty_max:,.0f} {a['qty_unit']}.")
    if a["cls"] == "vn":
        notes.append("Cổ phiếu mua phải chờ T+2,5 mới bán được — cắt lỗ chỉ thực hiện được sau khi hàng về.")

    out = {
        "atr": A, "atr_pct": A / entry * 100, "dec": dec, "side": side,
        "supports": [round(x, 6) for x in (below if s > 0 else above)[:3]],
        "resistances": [round(x, 6) for x in (above if s > 0 else below)[:3]],
        "stop": {"value": stop, "method": method, "dist_pct": R / entry * 100,
                 "alt": [{"label": "1×ATR (chặt)", "value": rnd(entry - s * A)},
                         {"label": "1,5×ATR", "value": rnd(vol_stop)},
                         {"label": "2,5×ATR (rộng)", "value": rnd(entry - s * 2.5 * A)}]},
        "target": {"value": target, "method": t_method, "rr": abs(target - entry) / R if R else None},
        "targets": targets, "budget": budget, "notes": notes, "disclaimer": DISCLAIMER,
    }

    # --- Vị thế đang mở: dời cắt lỗ / chốt lời theo giá hiện tại ---
    if price_now is not None:
        base_stop = stop_current or stop
        R0 = abs(entry - base_stop) if s * (entry - base_stop) > 0 else R  # cắt lỗ đã ở vùng lãi -> dùng R gợi ý
        r_now = s * (price_now - entry) / R0 if R0 else 0.0
        since = datetime.fromisoformat(opened_at).astimezone(VN_TZ).date() if opened_at else None
        ext = [x for x in (b if key != "SJC" else [])
               if since is None or datetime.fromtimestamp(x["t"], VN_TZ).date() >= since]
        extreme = (max([x["h"] for x in ext] + [price_now]) if s > 0
                   else min([x["l"] for x in ext] + [price_now]))
        mult = 2 if (risk_score or 0) >= 60 else 3
        chand = extreme - s * mult * A
        new_stop, action = base_stop, "Giữ nguyên cắt lỗ"
        reason = f"lãi hiện tại {r_now:+.2f}R — chưa đủ 1R để dời cắt lỗ"
        if r_now >= 1 and s * (entry - new_stop) > 0:
            new_stop, action = entry, "Dời cắt lỗ về hoà vốn"
            reason = f"lãi đã đạt {r_now:.2f}R ≥ 1R — khoá rủi ro của lệnh về 0"
        if r_now >= 2 and s * (chand - new_stop) > 0:
            new_stop, action = chand, f"Dời cắt lỗ theo xu hướng ({'đỉnh' if s > 0 else 'đáy'} − {mult}×ATR)"
            reason = (f"lãi {r_now:.2f}R ≥ 2R — Chandelier: {'đỉnh cao nhất' if s > 0 else 'đáy thấp nhất'} từ khi "
                      f"vào lệnh {extreme:,.{dec}f} − {mult}×ATR"
                      + (" (siết lại vì rủi ro thị trường Cao)" if mult == 2 else ""))
        if new_stop != base_stop and s * (price_now - new_stop) < 0.5 * A:
            new_stop = price_now - s * 0.5 * A  # không đặt sát giá hiện tại hơn 0,5×ATR
        if s * (new_stop - base_stop) < 0:
            new_stop = base_stop  # cắt lỗ chỉ dời theo hướng có lợi, không bao giờ nới ra
        new_stop = rnd(new_stop)
        pos = {"r_now": r_now, "stop": new_stop, "stop_action": action, "stop_reason": reason,
               "changed": stop_current is not None and abs(new_stop - stop_current) > 1e-9,
               "extreme": extreme, "lock_pct": s * (new_stop - entry) / entry * 100}
        tgt = target_current or target
        if s * (price_now - tgt) >= 0:
            nxt = next((x for x in obstacle if s * (x - price_now) > 0.3 * A), None)
            new_tgt = rnd(nxt - s * 0.1 * A) if nxt is not None else rnd(entry + s * 3 * R0)
            if s * (new_tgt - price_now) <= 0:
                new_tgt = rnd(price_now + s * 2 * A)
            pos.update(target=new_tgt, target_changed=True,
                       target_action="Giá đã vượt chốt lời — cân nhắc chốt một phần và dời mục tiêu",
                       target_reason=(f"{'kháng cự' if s > 0 else 'hỗ trợ'} kế tiếp {nxt:,.{dec}f}" if nxt is not None
                                      else "mục tiêu 3R / thêm 2×ATR"))
        else:
            pos.update(target=tgt, target_changed=target_current is None,
                       target_action="Giữ mục tiêu chốt lời",
                       target_reason=f"còn {abs(tgt - price_now) / A:.1f}×ATR tới mục tiêu")
        out["position"] = pos
    return out
