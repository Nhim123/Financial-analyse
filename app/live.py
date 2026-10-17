"""Giá trực tiếp: vòng lấy giá nhanh (mặc định 15 giây), tách khỏi chu kỳ kiểm chứng 10 phút.

Mỗi vòng chỉ 2 yêu cầu mạng: Binance (crypto) + CNBC (chỉ số, hàng hoá, tỷ giá). Giá trực
tiếp lấy từ MỘT nguồn mỗi mã để đủ nhanh; việc đối chiếu đa nguồn vẫn do pipeline 10 phút làm.
Dữ liệu chỉ giữ trong bộ nhớ — giao diện nhận qua luồng /stream (Server-Sent Events).
"""
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from .collectors.http import get_client, now_iso
from .config import COMMODITIES, CRYPTO, FX, STOCKS

# Tỷ giá trên CNBC: (mã, có cần đảo chiều không) để về quy ước "1 USD = X ngoại tệ".
# EUR= và GBP= niêm yết theo USD cho 1 EUR/GBP nên phải đảo.
_CNBC_FX = {"EUR": ("EUR=", True), "GBP": ("GBP=", True), "JPY": ("JPY=", False),
            "CNY": ("CNY=", False), "VND": ("VND=", False)}

# Giá có giờ khớp lệnh cũ hơn mức này coi như thị trường đang đóng cửa.
_LIVE_MAX_AGE_MIN = 20

_lock = threading.Lock()
_quotes: Dict[str, dict] = {}
_version = 0        # tăng mỗi khi có giá mới
_run_version = 0    # tăng mỗi khi chu kỳ thu thập 10 phút hoàn tất (giao diện làm mới tin)
_last_ok: Optional[str] = None
_last_error: Optional[str] = None


def _pct(s) -> Optional[float]:
    """'+0.59%' -> 0.59; 'UNCH' -> 0.0; không đọc được -> None."""
    s = str(s or "").strip()
    if s.upper() == "UNCH":
        return 0.0
    try:
        return float(s.replace("%", "").replace("+", "").replace(",", ""))
    except ValueError:
        return None


def _parse_time(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s.strip())  # -0400 -> -04:00
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else None  # chỉ có ngày (vd '2026-10-09') -> coi như không rõ giờ


def _fetch_binance(client) -> Dict[str, dict]:
    syms = json.dumps([c["binance"] for c in CRYPTO], separators=(",", ":"))
    r = client.get(f"https://api.binance.com/api/v3/ticker/24hr?symbols={syms}")
    r.raise_for_status()
    rows = {row["symbol"]: row for row in r.json()}
    ts = now_iso()
    out = {}
    for c in CRYPTO:
        row = rows.get(c["binance"])
        if row:
            out[c["key"]] = {"price": float(row["lastPrice"]),
                             "chg": float(row["priceChangePercent"]),
                             "src": "Binance", "qt": ts}
    return out


def _fetch_cnbc(client) -> Dict[str, dict]:
    targets = [(i["key"], i["cnbc"], False) for i in STOCKS + COMMODITIES]
    targets += [(f["key"], *_CNBC_FX[f["ccy"]]) for f in FX if f["ccy"] in _CNBC_FX]
    url = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
           f"?symbols={'|'.join(t[1] for t in targets)}&requestMethod=itv&exthrs=1&fund=1&output=json")
    r = client.get(url)
    r.raise_for_status()
    by_sym = {q.get("symbol"): q for q in r.json()["FormattedQuoteResult"]["FormattedQuote"]}
    out = {}
    for key, sym, invert in targets:
        q = by_sym.get(sym)
        try:
            price = float(str(q.get("last")).replace(",", "")) if q else None
        except ValueError:
            price = None
        if not price:
            continue
        chg = _pct(q.get("change_pct"))
        if invert:
            price = 1 / price
            if chg is not None:
                chg = (1 / (1 + chg / 100) - 1) * 100
        qt = _parse_time(q.get("last_time"))
        out[key] = {"price": price, "chg": chg, "src": "CNBC",
                    "qt": qt.astimezone(timezone.utc).isoformat(timespec="seconds") if qt else None}
    return out


# ---------------------------------------------------------------------------
# Chứng khoán Việt Nam: rổ VN30 / HNX30 và chỉ số tương ứng (SSI iBoard, không cần key).
# Giá cổ phiếu tính bằng VND. Thành phần rổ lấy trực tiếp từ SSI nên tự cập nhật khi đổi rổ.
# ---------------------------------------------------------------------------
VN_GROUPS = ("VN30", "HNX30")
_SSI = "https://iboard-query.ssi.com.vn"
_SSI_HEADERS = {"Origin": "https://iboard.ssi.com.vn", "Referer": "https://iboard.ssi.com.vn/"}
_VN_TZ = timezone(timedelta(hours=7))
_VN_IDLE_REFRESH_S = 600  # ngoài giờ giao dịch chỉ lấy lại mỗi 10 phút
_vn_last_fetch = 0.0


def vn_session_open(now: Optional[datetime] = None) -> bool:
    """Trong giờ giao dịch HOSE/HNX: thứ 2–6, 9:00–15:00 giờ Việt Nam."""
    t = (now or datetime.now(timezone.utc)).astimezone(_VN_TZ)
    return t.weekday() < 5 and 9 * 60 <= t.hour * 60 + t.minute <= 15 * 60


def _num(x) -> Optional[float]:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _fetch_ssi_vn(client) -> Dict[str, dict]:
    session = vn_session_open()
    out = {}
    for g in VN_GROUPS:
        r = client.get(f"{_SSI}/stock/group/{g}", headers=_SSI_HEADERS)
        r.raise_for_status()
        for s in r.json().get("data", []):
            sym = s.get("stockSymbol")
            price = _num(s.get("matchedPrice")) or _num(s.get("refPrice"))  # chưa khớp lệnh -> giá tham chiếu
            if not sym or not price:
                continue
            out[f"VN:{sym}"] = {"price": price, "chg": _num(s.get("priceChangePercent")),
                                "src": "SSI", "qt": None, "live": session, "group": g,
                                "name": s.get("companyNameVi") or sym}
        r = client.get(f"{_SSI}/exchange-index/{g}", headers=_SSI_HEADERS)
        r.raise_for_status()
        d = r.json().get("data") or {}
        if d.get("indexValue"):
            qt = (datetime.fromtimestamp(d["time"] / 1000, tz=timezone.utc).isoformat(timespec="seconds")
                  if d.get("time") else None)
            out[g] = {"price": float(d["indexValue"]), "chg": _num(d.get("changePercent")),
                      "src": "SSI", "qt": qt, "live": session, "group": "index"}
    return out


def refresh() -> None:
    """Một vòng lấy giá. Nguồn nào lỗi thì giữ giá cũ của nguồn đó, không làm trống giao diện."""
    global _version, _last_ok, _last_error, _vn_last_fetch
    fresh: Dict[str, dict] = {}
    errors = []
    fetchers = [_fetch_binance, _fetch_cnbc]
    if vn_session_open() or time.time() - _vn_last_fetch >= _VN_IDLE_REFRESH_S:
        fetchers.append(_fetch_ssi_vn)
    with get_client() as client:
        for fn in fetchers:
            try:
                fresh.update(fn(client))
                if fn is _fetch_ssi_vn:
                    _vn_last_fetch = time.time()
            except Exception as e:  # noqa: BLE001
                errors.append(f"{fn.__name__[7:]}: {type(e).__name__}")
    now = datetime.now(timezone.utc)
    for q in fresh.values():
        if "live" in q:  # chứng khoán VN: trạng thái phiên tính theo giờ giao dịch
            continue
        qt = datetime.fromisoformat(q["qt"]) if q["qt"] else None
        age_s = (now - qt).total_seconds() if qt else None
        # CNBC đôi khi trả giờ khớp lệnh ở TƯƠNG LAI cho hợp đồng tương lai khi sàn đã nghỉ
        # (vd vàng cuối tuần) -> chỉ tin mốc trong khoảng [-2 phút, +20 phút] tính tới hiện tại.
        q["live"] = age_s is not None and -120 <= age_s <= _LIVE_MAX_AGE_MIN * 60
    with _lock:
        for k, q in fresh.items():
            old = _quotes.get(k)
            q["dir"] = 0 if not old else (q["price"] > old["price"]) - (q["price"] < old["price"])
            _quotes[k] = q
        if fresh:
            _version += 1
            _last_ok = now_iso()
        _last_error = "; ".join(errors) or None


def bump_run() -> None:
    global _run_version
    with _lock:
        _run_version += 1


def versions() -> Tuple[int, int]:
    with _lock:
        return _version, _run_version


def quotes() -> Dict[str, dict]:
    with _lock:
        return {k: dict(q) for k, q in _quotes.items()}


def snapshot() -> dict:
    with _lock:
        return {"v": _version, "rv": _run_version, "ts": _last_ok, "error": _last_error,
                "quotes": {k: dict(q) for k, q in _quotes.items()}}
