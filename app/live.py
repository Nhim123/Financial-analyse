"""Giá trực tiếp: vòng lấy giá nhanh (mặc định 15 giây), tách khỏi chu kỳ kiểm chứng 10 phút.

Nguồn: Binance (crypto) + CNBC (chỉ số, hàng hoá, tỷ giá) + SSI iBoard (chứng khoán Việt Nam).
Giá trực tiếp lấy từ MỘT nguồn mỗi mã để đủ nhanh; việc đối chiếu đa nguồn vẫn do pipeline 10
phút làm. Dữ liệu chỉ giữ trong bộ nhớ — giao diện nhận qua luồng /stream (Server-Sent Events),
mỗi lần chỉ gửi các mã vừa đổi giá (theo số phiên bản `ver` của từng mã).
"""
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from .collectors.http import get_client, now_iso
from .config import (COMMODITIES, CRYPTO, EXTRA_COMMODITIES, EXTRA_CRYPTO, FX, FX_PAIRS, STOCKS,
                     VN_BOARD_INTERVAL_SECONDS, VN_INDICES, VN_LIVE_INTERVAL_SECONDS, VN_SECTORS)

# Tỷ giá trên CNBC: (mã, có cần đảo chiều không) để về quy ước "1 USD = X ngoại tệ".
# EUR= và GBP= niêm yết theo USD cho 1 EUR/GBP nên phải đảo.
_CNBC_FX = {"EUR": ("EUR=", True), "GBP": ("GBP=", True), "JPY": ("JPY=", False),
            "CNY": ("CNY=", False), "VND": ("VND=", False)}

# Giá có giờ khớp lệnh cũ hơn mức này coi như thị trường đang đóng cửa.
_LIVE_MAX_AGE_MIN = 20

_lock = threading.Lock()
_quotes: Dict[str, dict] = {}
_groups: Dict[str, List[str]] = {}  # rổ/sàn chứng khoán VN -> danh sách mã (VN30, HNX30, HOSE)
_version = 0        # tăng mỗi khi có giá mới
_run_version = 0    # tăng mỗi khi chu kỳ thu thập 10 phút hoàn tất (giao diện làm mới tin)
_vn_version = 0     # tăng khi số liệu tổng hợp CK VN đổi (KL, độ rộng, thanh khoản…) -> làm mới bảng giá
_last_ok: Optional[str] = None
_last_error: Optional[str] = None

# Trường gửi xuống trình duyệt qua /stream (các trường còn lại chỉ dùng khi dựng trang).
_SSE_FIELDS = ("price", "chg", "dir", "live", "src")


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
    coins = CRYPTO + EXTRA_CRYPTO
    syms = json.dumps([c["binance"] for c in coins], separators=(",", ":"))
    r = client.get(f"https://api.binance.com/api/v3/ticker/24hr?symbols={syms}")
    r.raise_for_status()
    rows = {row["symbol"]: row for row in r.json()}
    ts = now_iso()
    out = {}
    for c in coins:
        row = rows.get(c["binance"])
        if row:
            out[c["key"]] = {"price": float(row["lastPrice"]),
                             "chg": float(row["priceChangePercent"]),
                             "vol": float(row["quoteVolume"]),  # KL 24h quy ra USDT (riêng Binance)
                             "src": "Binance", "qt": ts}
    return out


def _fetch_cnbc(client) -> Dict[str, dict]:
    targets = [(i["key"], i["cnbc"], False) for i in STOCKS + COMMODITIES + EXTRA_COMMODITIES]
    targets += [(f["key"], *_CNBC_FX[f["ccy"]]) for f in FX if f["ccy"] in _CNBC_FX]
    targets += [(p["key"], p["cnbc"], False) for p in FX_PAIRS if p.get("cnbc")]
    symbols = list(dict.fromkeys(t[1] for t in targets))  # bỏ trùng (EUR= dùng cho 2 key)
    url = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
           f"?symbols={'|'.join(symbols)}&requestMethod=itv&exthrs=1&fund=1&output=json")
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
    # EUR/VND suy ra từ EUR/USD × USD/VND (CNBC không niêm yết trực tiếp).
    eu, uv = out.get("EURUSD"), out.get("USDVND")
    if eu and uv:
        chg = None
        if eu["chg"] is not None and uv["chg"] is not None:
            chg = ((1 + eu["chg"] / 100) * (1 + uv["chg"] / 100) - 1) * 100
        qts = [q for q in (eu["qt"], uv["qt"]) if q]
        out["EURVND"] = {"price": eu["price"] * uv["price"], "chg": chg, "src": "CNBC",
                         "qt": min(qts) if qts else None}
    return out


# ---------------------------------------------------------------------------
# Chứng khoán Việt Nam (SSI iBoard, không cần key). Giá cổ phiếu tính bằng VND.
# Job riêng refresh_vn(), tách khỏi vòng giá thế giới, chạy mỗi VN_LIVE_INTERVAL_SECONDS:
# - Trong giờ khớp lệnh: nhanh (VN30, HNX30 + 5 chỉ số sàn) mỗi VN_LIVE_INTERVAL_SECONDS,
#   chậm (toàn bộ mã HOSE: bảng giá, KL, dòng tiền, khối ngoại + chỉ số ngành) mỗi VN_BOARD_INTERVAL_SECONDS.
# - Nghỉ trưa / sau ATC (vẫn trong phiên, giá gần như đứng): mọi phần mỗi phút.
# - Ngoài giờ giao dịch: mỗi 10 phút. Lỗi SSI -> tạm nghỉ để không dồn request.
# ---------------------------------------------------------------------------
VN_GROUPS = ("VN30", "HNX30")
_SSI = "https://iboard-query.ssi.com.vn"
_SSI_HEADERS = {"Origin": "https://iboard.ssi.com.vn", "Referer": "https://iboard.ssi.com.vn/"}
_VN_TZ = timezone(timedelta(hours=7))
_VN_IDLE_REFRESH_S = 600
_VN_PAUSE_REFRESH_S = 60
_VN_ERROR_BACKOFF_S = 30
_vn_last = {"fast": 0.0, "slow": 0.0, "error": 0.0}
# Giữ kết nối tới SSI giữa các nhịp: mở kết nối TLS mới mỗi lần tốn ~1,5 giây/request, dùng lại ~0,3 giây.
_vn_client = None
# Trường quyết định các ô tổng hợp của tab Cổ phiếu (ngoài giá/%) — đổi thì báo giao diện làm mới.
_VN_AGG_FIELDS = ("price", "chg", "vol", "val", "qty", "adv", "dec", "flat", "bu", "sd", "fbv", "fsv")

# Phiên khớp lệnh liên tục + ATO/ATC (phút tính từ 0:00 giờ VN): 9:00–11:30 và 13:00–14:45.
_VN_MATCHING = ((9 * 60, 11 * 60 + 30), (13 * 60, 14 * 60 + 45))


def _vn_minutes(now: Optional[datetime]) -> Tuple[int, int]:
    t = (now or datetime.now(timezone.utc)).astimezone(_VN_TZ)
    return t.weekday(), t.hour * 60 + t.minute


def vn_session_open(now: Optional[datetime] = None) -> bool:
    """Trong giờ giao dịch HOSE/HNX: thứ 2–6, 9:00–15:00 giờ Việt Nam."""
    wd, m = _vn_minutes(now)
    return wd < 5 and 9 * 60 <= m <= 15 * 60


def vn_matching(now: Optional[datetime] = None) -> bool:
    """Đang khớp lệnh (giá thay đổi liên tục): 9:00–11:30, 13:00–14:45; không gồm nghỉ trưa."""
    wd, m = _vn_minutes(now)
    return wd < 5 and any(a <= m < b for a, b in _VN_MATCHING)


def _num(x) -> Optional[float]:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _ssi(client, path: str):
    r = client.get(f"{_SSI}{path}", headers=_SSI_HEADERS)
    r.raise_for_status()
    return r.json().get("data")


def _vn_stock(s: dict, session: bool) -> Optional[Tuple[str, dict]]:
    sym = s.get("stockSymbol")
    price = _num(s.get("matchedPrice")) or _num(s.get("refPrice"))  # chưa khớp lệnh -> giá tham chiếu
    if not sym or not price:
        return None
    return f"VN:{sym}", {
        "price": price, "chg": _num(s.get("priceChangePercent")), "ref": _num(s.get("refPrice")),
        "ceil": _num(s.get("ceiling")), "floor": _num(s.get("floor")),
        "src": "SSI", "qt": None, "live": session, "name": s.get("companyNameVi") or sym,
        "vol": _num(s.get("nmTotalTradedQty")), "val": _num(s.get("nmTotalTradedValue")),
        "avg": _num(s.get("avgPrice")), "bu": _num(s.get("stockBUVol")), "sd": _num(s.get("stockSDVol")),
        "fbv": _num(s.get("buyForeignValue")), "fsv": _num(s.get("sellForeignValue")),
    }


def _vn_index(d: dict, session: bool) -> Optional[dict]:
    if not d or not d.get("indexValue"):
        return None
    qt = (datetime.fromtimestamp(d["time"] / 1000, tz=timezone.utc).isoformat(timespec="seconds")
          if d.get("time") else None)
    return {"price": float(d["indexValue"]), "chg": _num(d.get("changePercent")), "src": "SSI",
            "qt": qt, "live": session, "adv": d.get("advances"), "dec": d.get("declines"),
            "flat": d.get("nochanges"), "val": _num(d.get("totalValue")), "qty": _num(d.get("totalQtty")),
            "fbq": _num(d.get("totalBuyForeignQtty")), "fsq": _num(d.get("totalSellForeignQtty"))}


def _fetch_vn(client, slow: bool) -> Tuple[Dict[str, dict], Dict[str, List[str]]]:
    session = vn_session_open()
    jobs = {f"group:{g}": f"/stock/group/{g}" for g in VN_GROUPS}
    jobs.update({f"index:{key}": f"/exchange-index/{ssi}" for key, _, ssi in VN_INDICES})
    if slow:
        jobs["exchange:HOSE"] = "/stock/exchange/hose"
        jobs.update({f"index:SEC:{code}": f"/exchange-index/{code}" for code, _ in VN_SECTORS})
    with ThreadPoolExecutor(max_workers=min(len(jobs), 10)) as pool:
        futures = {name: pool.submit(_ssi, client, path) for name, path in jobs.items()}
    out, groups, failed = {}, {}, []
    for name, fut in futures.items():
        try:
            data = fut.result()
        except Exception:  # noqa: BLE001 — một phần lỗi thì giữ dữ liệu cũ phần đó
            failed.append(name)
            continue
        kind, _, ident = name.partition(":")
        if kind == "index":
            q = _vn_index(data or {}, session)
            if q:
                out[ident] = q
        else:
            syms = []
            for s in data or []:
                row = _vn_stock(s, session)
                if row:
                    out[row[0]] = row[1]
                    syms.append(row[0][3:])
            if syms:
                groups[ident] = syms
    if failed and not out:
        raise RuntimeError("SSI: " + ", ".join(failed))
    return out, groups


def _store(fresh: Dict[str, dict]) -> None:
    """Ghi giá mới; mã nào đổi giá/% /trạng thái phiên thì nhận phiên bản mới (để gửi delta)."""
    global _version, _last_ok
    nv = _version + 1
    for k, q in fresh.items():
        old = _quotes.get(k)
        q["dir"] = 0 if not old else (q["price"] > old["price"]) - (q["price"] < old["price"])
        changed = (not old or old["price"] != q["price"] or old.get("chg") != q.get("chg")
                   or old.get("live") != q.get("live"))
        q["ver"] = nv if changed else old.get("ver", nv)
        _quotes[k] = q
    if fresh:
        _version = nv
        _last_ok = now_iso()


_errors: Dict[str, Optional[str]] = {}


def _set_error(source: str, err: Optional[str]) -> None:
    """Lỗi theo từng nguồn (vòng giá thế giới và vòng CK VN chạy riêng, không ghi đè lỗi của nhau)."""
    global _last_error
    _errors[source] = err
    _last_error = "; ".join(e for e in _errors.values() if e) or None


def refresh() -> None:
    """Một vòng lấy giá thế giới (crypto, chỉ số, hàng hoá, tỷ giá). Nguồn nào lỗi thì giữ giá cũ
    của nguồn đó, không làm trống giao diện. Chứng khoán VN do refresh_vn() lo."""
    fresh: Dict[str, dict] = {}
    errors = []
    with get_client() as client:
        for fn in (_fetch_binance, _fetch_cnbc):
            try:
                fresh.update(fn(client))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{fn.__name__[7:]}: {type(e).__name__}")
    now = datetime.now(timezone.utc)
    for q in fresh.values():
        qt = datetime.fromisoformat(q["qt"]) if q["qt"] else None
        age_s = (now - qt).total_seconds() if qt else None
        # CNBC đôi khi trả giờ khớp lệnh ở TƯƠNG LAI cho hợp đồng tương lai khi sàn đã nghỉ
        # (vd vàng cuối tuần) -> chỉ tin mốc trong khoảng [-2 phút, +20 phút] tính tới hiện tại.
        q["live"] = age_s is not None and -120 <= age_s <= _LIVE_MAX_AGE_MIN * 60
    with _lock:
        _store(fresh)
        _set_error("world", "; ".join(errors) or None)


def refresh_vn() -> None:
    """Một nhịp của vòng CK Việt Nam; tự quyết định phần nào đến hạn lấy theo giờ giao dịch."""
    global _vn_version
    now_s = time.time()
    if now_s - _vn_last["error"] < _VN_ERROR_BACKOFF_S:
        return
    if vn_matching():
        fast_every, slow_every = VN_LIVE_INTERVAL_SECONDS, VN_BOARD_INTERVAL_SECONDS
    elif vn_session_open():
        fast_every = slow_every = _VN_PAUSE_REFRESH_S
    else:
        fast_every = slow_every = _VN_IDLE_REFRESH_S
    # Trừ 1 giây: job chạy đúng nhịp VN_LIVE_INTERVAL_SECONDS, lệch vài ms không được bỏ lượt.
    slow_due = now_s - _vn_last["slow"] >= slow_every - 1
    fast_due = slow_due or now_s - _vn_last["fast"] >= fast_every - 1
    if not fast_due:
        return
    global _vn_client
    try:
        if _vn_client is None:
            _vn_client = get_client(keepalive_s=120)
        fresh, groups = _fetch_vn(_vn_client, slow_due)
    except Exception as e:  # noqa: BLE001
        if _vn_client is not None:  # bỏ kết nối có thể đã hỏng, nhịp sau mở lại
            _vn_client.close()
            _vn_client = None
        _vn_last["error"] = now_s
        with _lock:
            _set_error("vn", f"ssi: {type(e).__name__}")
        return
    _vn_last["fast"] = now_s
    if slow_due:
        _vn_last["slow"] = now_s
    with _lock:
        agg_changed = any(
            tuple(q.get(f) for f in _VN_AGG_FIELDS) != tuple((_quotes.get(k) or {}).get(f) for f in _VN_AGG_FIELDS)
            for k, q in fresh.items())
        _store(fresh)
        if any(_groups.get(g) != s for g, s in groups.items()):
            agg_changed = True
        _groups.update(groups)
        if agg_changed:
            _vn_version += 1
        _set_error("vn", None)


def merge(fresh: Dict[str, dict]) -> None:
    """Nhận giá từ module khác (vd vàng SJC lấy theo chu kỳ chậm) vào cùng luồng trực tiếp."""
    with _lock:
        _store({k: dict(q) for k, q in fresh.items()})


def bump_run() -> None:
    global _run_version
    with _lock:
        _run_version += 1


def versions() -> Tuple[int, int, int]:
    with _lock:
        return _version, _run_version, _vn_version


def quotes() -> Dict[str, dict]:
    with _lock:
        return {k: dict(q) for k, q in _quotes.items()}


def groups() -> Dict[str, List[str]]:
    with _lock:
        return {g: list(s) for g, s in _groups.items()}


def snapshot(since: int = 0) -> dict:
    """Gói gửi qua /stream: chỉ các mã có phiên bản > since (since=0 -> toàn bộ)."""
    with _lock:
        return {"v": _version, "rv": _run_version, "vv": _vn_version, "ts": _last_ok, "error": _last_error,
                "quotes": {k: {f: q.get(f) for f in _SSE_FIELDS}
                           for k, q in _quotes.items() if q.get("ver", 0) > since}}
