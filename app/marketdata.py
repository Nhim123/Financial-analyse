"""Dữ liệu thị trường theo chu kỳ chậm (mặc định 5 phút) cho giao diện 4 tab:

- Vàng SJC (bảng giá PNJ) -> đưa vào luồng giá trực tiếp (key "SJC", triệu đồng/lượng).
- Tỷ giá mua/bán Vietcombank.
- Lịch sự kiện kinh tế trong tuần (ForexFactory), tiêu đề dịch tiếng Việt (có cache).
- Vốn hoá & khối lượng 24h crypto (CoinGecko).
- Sparkline: crypto 24h (Binance nến 1 giờ), BTC 24h (nến 15 phút), chỉ số & cổ phiếu Việt Nam
  trong phiên (SSI nến 15 phút); các mã còn lại lấy mẫu giá trực tiếp mỗi chu kỳ.
- Khối lượng khớp lệnh các phiên gần nhất (SSI nến ngày) để so với trung bình 20 phiên.
Mỗi phần lỗi độc lập: phần nào lỗi thì giữ dữ liệu cũ của phần đó.
"""
import json
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Deque, Dict, List, Optional

from . import econ_events, live, notifier, storage
from .collectors.http import get_client, now_iso
from .config import CRYPTO, EXTRA_CRYPTO, VN_INDICES
from .enrich import translate_vi

_VN_TZ = timezone(timedelta(hours=7))
_SSI_CHART = "https://iboard-api.ssi.com.vn/statistics/charts/history"
_SSI_HEADERS = {"Origin": "https://iboard.ssi.com.vn", "Referer": "https://iboard.ssi.com.vn/"}
_COINS = CRYPTO + EXTRA_CRYPTO

_lock = threading.Lock()
_data: dict = {"bank": {}, "bank_ts": None, "calendar": [], "caps": {}, "spark": {},
               "btc": [], "vn_days": [], "errors": {}}
_samples: Dict[str, Deque[float]] = {}
_last: Dict[str, float] = {}
_SAMPLE_LEN = 48  # 48 mẫu × 5 phút = 4 giờ


def _due(part: str, every_s: float) -> bool:
    return time.time() - _last.get(part, 0.0) >= every_s


def _num(x) -> Optional[float]:
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _vn_today() -> str:
    return datetime.now(_VN_TZ).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
def _gold(client) -> None:
    """Giá vàng miếng SJC tại PNJ (nghìn đồng/chỉ) -> triệu đồng/lượng; % so với ngày trước."""
    r = client.get("https://edge-api.pnj.io/ecom-frontend/v1/get-gold-price?zone=00")
    r.raise_for_status()
    row = next(d for d in r.json()["data"] if d.get("masp") == "SJC")
    sell, buy = row["giaban"] / 100, row["giamua"] / 100  # 14 360 nghìn/chỉ = 143,60 triệu/lượng
    days = json.loads(storage.kv_get("sjc_days") or "{}")
    today = _vn_today()
    days[today] = sell
    days = dict(sorted(days.items())[-10:])
    storage.kv_set("sjc_days", json.dumps(days))
    prev = [v for d, v in days.items() if d < today]
    chg = (sell / prev[-1] - 1) * 100 if prev else None
    live.merge({"SJC": {"price": sell, "chg": chg, "buy": buy, "src": "PNJ", "qt": now_iso(), "live": True}})


def _bank(client) -> None:
    """Tỷ giá Vietcombank: mua chuyển khoản / bán (VND cho 1 đơn vị ngoại tệ)."""
    r = client.get(f"https://www.vietcombank.com.vn/api/exchangerates?date={_vn_today()}")
    r.raise_for_status()
    j = r.json()
    bank = {x["currencyCode"]: {"buy": _num(x.get("transfer")), "cash": _num(x.get("cash")),
                                "sell": _num(x.get("sell"))} for x in j.get("Data", [])}
    if bank:
        with _lock:
            _data["bank"], _data["bank_ts"] = bank, j.get("UpdatedDate")


# Thuật ngữ lịch kinh tế: dịch máy hay sai (vd "3m/y" thành "3 triệu/năm") nên các chỉ báo phổ
# biến dùng bảng chuẩn; hậu tố kỳ so sánh và tên nước được tách riêng trước khi tra/dịch.
_CAL_TERMS = {
    "CPI": "Chỉ số giá tiêu dùng CPI", "Core CPI": "CPI lõi", "Trimmed CPI": "CPI tinh giản",
    "Median CPI": "CPI trung vị", "Common CPI": "CPI phổ biến", "Flash CPI": "CPI sơ bộ",
    "Core Flash CPI": "CPI lõi sơ bộ", "PPI": "Chỉ số giá sản xuất PPI", "Core PPI": "PPI lõi",
    "Core PCE Price Index": "Chỉ số giá PCE lõi", "PCE Price Index": "Chỉ số giá PCE",
    "GDP": "Tăng trưởng GDP", "Advance GDP": "GDP sơ bộ", "Prelim GDP": "GDP sơ bộ",
    "Final GDP": "GDP chính thức", "Retail Sales": "Doanh số bán lẻ", "Core Retail Sales": "Doanh số bán lẻ lõi",
    "Unemployment Rate": "Tỷ lệ thất nghiệp", "Unemployment Claims": "Đơn xin trợ cấp thất nghiệp",
    "Claimant Count Change": "Số người nhận trợ cấp thất nghiệp",
    "Non-Farm Employment Change": "Bảng lương phi nông nghiệp NFP",
    "ADP Non-Farm Employment Change": "Việc làm khu vực tư nhân ADP", "Employment Change": "Thay đổi việc làm",
    "Average Earnings Index": "Chỉ số thu nhập bình quân", "Average Hourly Earnings": "Thu nhập bình quân theo giờ",
    "Trade Balance": "Cán cân thương mại", "Current Account": "Cán cân vãng lai",
    "Industrial Production": "Sản xuất công nghiệp", "Manufacturing PMI": "PMI sản xuất",
    "Services PMI": "PMI dịch vụ", "Flash Manufacturing PMI": "PMI sản xuất sơ bộ",
    "Flash Services PMI": "PMI dịch vụ sơ bộ", "ISM Manufacturing PMI": "PMI sản xuất ISM",
    "ISM Services PMI": "PMI dịch vụ ISM", "Building Permits": "Giấy phép xây dựng",
    "Housing Starts": "Số nhà khởi công", "Existing Home Sales": "Doanh số nhà hiện có",
    "New Home Sales": "Doanh số nhà mới", "Pending Home Sales": "Doanh số nhà chờ bán",
    "Durable Goods Orders": "Đơn hàng hàng lâu bền", "Core Durable Goods Orders": "Đơn hàng lâu bền lõi",
    "CB Consumer Confidence": "Niềm tin tiêu dùng CB", "Consumer Confidence": "Niềm tin người tiêu dùng",
    "Prelim UoM Consumer Sentiment": "Tâm lý tiêu dùng UoM sơ bộ",
    "Revised UoM Consumer Sentiment": "Tâm lý tiêu dùng UoM điều chỉnh",
    "Crude Oil Inventories": "Tồn kho dầu thô Mỹ", "Natural Gas Storage": "Dự trữ khí tự nhiên",
    "Federal Funds Rate": "Lãi suất quỹ liên bang Fed", "FOMC Statement": "Tuyên bố của FOMC",
    "FOMC Press Conference": "Họp báo FOMC", "FOMC Meeting Minutes": "Biên bản họp FOMC",
    "Main Refinancing Rate": "Lãi suất tái cấp vốn ECB", "ECB Press Conference": "Họp báo ECB",
    "Monetary Policy Statement": "Tuyên bố chính sách tiền tệ", "Official Bank Rate": "Lãi suất cơ bản BoE",
    "Overnight Rate": "Lãi suất qua đêm", "Cash Rate": "Lãi suất tiền mặt", "BOJ Policy Rate": "Lãi suất chính sách BoJ",
    "1-y Loan Prime Rate": "Lãi suất cho vay cơ bản 1 năm LPR", "5-y Loan Prime Rate": "Lãi suất cho vay cơ bản 5 năm LPR",
    "Empire State Manufacturing Index": "Chỉ số sản xuất Empire State",
    "Philly Fed Manufacturing Index": "Chỉ số sản xuất Philly Fed", "ifo Business Climate": "Chỉ số môi trường kinh doanh Ifo",
    "ZEW Economic Sentiment": "Tâm lý kinh tế ZEW", "Bank Holiday": "Nghỉ lễ ngân hàng",
    "IMF Meetings": "Hội nghị IMF", "Fixed Asset Investment ytd/y": "Đầu tư tài sản cố định (lũy kế, so cùng kỳ)",
}
_CAL_SUFFIX = {"m/m": "so tháng trước", "y/y": "so cùng kỳ", "q/q": "so quý trước", "w/w": "so tuần trước",
               "3m/y": "3 tháng, so cùng kỳ", "q/y": "so cùng kỳ", "ytd/y": "lũy kế, so cùng kỳ"}
_CAL_COUNTRY = {"German": "Đức", "French": "Pháp", "Italian": "Ý", "Spanish": "Tây Ban Nha"}


def _cal_vi(title: str, client) -> str:
    base, suffix, nation = title.strip(), "", ""
    for adj, vi in _CAL_COUNTRY.items():
        if base.startswith(adj + " "):
            base, nation = base[len(adj) + 1:], vi
    head, _, tail = base.rpartition(" ")
    if head and tail in _CAL_SUFFIX:
        base, suffix = head, _CAL_SUFFIX[tail]
    text = _CAL_TERMS.get(title) or _CAL_TERMS.get(base) or translate_vi(base, client)
    extra = ", ".join(x for x in (nation, suffix) if x)
    return f"{text} ({extra})" if extra else text


def _calendar(client) -> None:
    """Lịch sự kiện kinh tế tuần này (giờ VN); dịch tiêu đề và lưu bản dịch để dùng lại.

    Bản lịch gần nhất được lưu lại: khởi động lại đúng lúc nguồn lỗi / giới hạn lượt gọi (HTTP 429)
    vẫn hiển thị được lịch đã có thay vì để trống.
    """
    r = client.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json")
    if r.status_code != 200:
        with _lock:
            empty = not _data["calendar"]
        if empty:
            cached = json.loads(storage.kv_get("calendar_last") or "[]")
            _apply_actuals(cached, json.loads(storage.kv_get("cal_actuals") or "{}"))
            with _lock:
                _data["calendar"] = cached
        r.raise_for_status()
    cache = json.loads(storage.kv_get("calendar_vi2") or "{}")
    events = []
    for e in r.json():
        imp = {"High": 3, "Medium": 2, "Low": 1}.get(e.get("impact"))
        if not imp:
            continue  # bỏ ngày nghỉ lễ / sự kiện không phải số liệu kinh tế
        title = e.get("title", "").strip()
        if title and title not in cache:
            cache[title] = _cal_vi(title, client)
        dt = datetime.fromisoformat(e["date"]).astimezone(_VN_TZ)
        events.append({"dt": dt.isoformat(), "title": title, "vi": cache.get(title) or title,
                       "country": e.get("country", ""), "imp": imp,
                       "forecast": e.get("forecast") or "", "previous": e.get("previous") or ""})
    storage.kv_set("calendar_vi2", json.dumps(cache, ensure_ascii=False))
    storage.kv_set("calendar_last", json.dumps(events, ensure_ascii=False))
    _apply_actuals(events, json.loads(storage.kv_get("cal_actuals") or "{}"))
    with _lock:
        _data["calendar"] = sorted(events, key=lambda x: x["dt"])


def _apply_actuals(events: List[dict], actuals: dict) -> None:
    """Gắn số thực tế + đánh giá so với kỳ vọng vào từng sự kiện đã công bố."""
    for e in events:
        a = actuals.get(econ_events.key(e))
        if a:
            e["actual"] = a
            e["eval"] = econ_events.evaluate(e)


def _actuals(client) -> None:
    """Số thực tế của các sự kiện đã tới giờ công bố (Nasdaq); sự kiện ảnh hưởng cao vừa có kết quả
    -> gửi Telegram (một lần mỗi sự kiện, chỉ khi công bố trong 6 giờ gần đây)."""
    with _lock:
        events = [dict(e) for e in _data["calendar"]]
    if not events:
        return
    actuals = json.loads(storage.kv_get("cal_actuals") or "{}")
    now = datetime.now(timezone.utc)
    due = [e for e in events if econ_events.key(e) not in actuals and (e["forecast"] or e["previous"])
           and datetime.fromisoformat(e["dt"]) <= now - timedelta(minutes=2)]
    days: Dict[str, List[dict]] = {}
    for e in due:  # gom theo ngày New York để mỗi ngày chỉ gọi API một lần
        ny_day = datetime.fromisoformat(e["dt"]).astimezone(econ_events._NY).date()
        days.setdefault(ny_day.isoformat(), []).append(e)
    for d, evs in days.items():
        rows = econ_events.fetch_day(client, date.fromisoformat(d))
        for e in evs:
            r = econ_events.match(e, rows)
            if r:
                actuals[econ_events.key(e)] = r["actual"].strip()
    week_keys = {econ_events.key(e) for e in events}
    actuals = {k: v for k, v in actuals.items() if k in week_keys}  # chỉ giữ tuần hiện tại
    storage.kv_set("cal_actuals", json.dumps(actuals, ensure_ascii=False))
    _apply_actuals(events, actuals)
    with _lock:
        _data["calendar"] = events
    _notify_results(events, now)


def _notify_results(events: List[dict], now: datetime) -> None:
    sent = set(json.loads(storage.kv_get("cal_sent") or "[]"))
    fresh = [e for e in events if e.get("eval") and e["imp"] >= 3 and econ_events.key(e) not in sent]
    for e in fresh:
        sent.add(econ_events.key(e))
        if now - datetime.fromisoformat(e["dt"]) <= timedelta(hours=6) and notifier.TELEGRAM_ENABLED:
            notifier.send_message(econ_events.telegram_text(e), silent=notifier._silent_now())
    if fresh:
        keys = {econ_events.key(e) for e in events}
        storage.kv_set("cal_sent", json.dumps(sorted(k for k in sent if k in keys)))


def _caps(client) -> None:
    ids = ",".join(c["coingecko"] for c in _COINS)
    r = client.get(f"https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids={ids}")
    r.raise_for_status()
    by_id = {c["coingecko"]: c["key"] for c in _COINS}
    caps = {by_id[x["id"]]: {"cap": x.get("market_cap"), "vol": x.get("total_volume")}
            for x in r.json() if x.get("id") in by_id}
    with _lock:
        _data["caps"] = caps


def _klines(client, symbol: str, interval: str, limit: int) -> List[float]:
    r = client.get(f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}")
    r.raise_for_status()
    return [float(k[4]) for k in r.json()]


def _crypto_sparks(client) -> None:
    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = {c["key"]: pool.submit(_klines, client, c["binance"], "1h", 25) for c in _COINS}
        btc = pool.submit(_klines, client, "BTCUSDT", "15m", 96)
    spark = {}
    for k, f in futs.items():
        try:
            spark[k] = f.result()
        except Exception:  # noqa: BLE001
            pass
    with _lock:
        _data["spark"].update(spark)
        try:
            _data["btc"] = btc.result()
        except Exception:  # noqa: BLE001
            pass


def _ssi_chart(client, symbol: str, resolution: str, days: int) -> dict:
    now = int(time.time())
    r = client.get(f"{_SSI_CHART}?resolution={resolution}&symbol={symbol}&from={now - days * 86400}&to={now}",
                   headers=_SSI_HEADERS)
    r.raise_for_status()
    return r.json().get("data") or {}


def _vn_days(client) -> None:
    """Tổng khối lượng khớp lệnh HOSE + HNX + UPCOM theo từng phiên (để tính TB20)."""
    per_day: Dict[str, float] = {}
    for _, _, ssi in [i for i in VN_INDICES if i[0] in ("VNINDEX", "HNX", "UPCOM")]:
        d = _ssi_chart(client, ssi, "1D", 45)
        for t, v in zip(d.get("t", []), d.get("v", [])):
            day = datetime.fromtimestamp(t, _VN_TZ).strftime("%Y-%m-%d")
            per_day[day] = per_day.get(day, 0.0) + float(v or 0)
    with _lock:
        _data["vn_days"] = [{"d": d, "v": v} for d, v in sorted(per_day.items())]


def _intraday(client, symbol: str) -> List[float]:
    """Giá đóng nến 15 phút của phiên gần nhất."""
    d = _ssi_chart(client, symbol, "15", 5)
    ts, cs = d.get("t", []), d.get("c", [])
    if not ts:
        return []
    last_day = datetime.fromtimestamp(ts[-1], _VN_TZ).date()
    return [c for t, c in zip(ts, cs) if datetime.fromtimestamp(t, _VN_TZ).date() == last_day]


def _vn_sparks(client) -> None:
    """Sparkline trong phiên: 5 chỉ số sàn + các mã đang hiển thị (thanh khoản lớn nhất HOSE,
    tăng/giảm mạnh nhất VN30 & HNX30)."""
    q, groups = live.quotes(), live.groups()
    targets = {key: ssi for key, _, ssi in VN_INDICES}
    hose = sorted((s for s in groups.get("HOSE", []) if f"VN:{s}" in q),
                  key=lambda s: -(q[f"VN:{s}"].get("val") or 0))
    picks = set(hose[:16])
    for g in ("VN30", "HNX30"):
        rows = sorted((s for s in groups.get(g, []) if q.get(f"VN:{s}", {}).get("chg") is not None),
                      key=lambda s: q[f"VN:{s}"]["chg"])
        picks.update(rows[:5] + rows[-5:])
    targets.update({f"VN:{s}": s for s in picks})
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {k: pool.submit(_intraday, client, sym) for k, sym in targets.items()}
    spark = {}
    for k, f in futs.items():
        try:
            pts = f.result()
            if len(pts) >= 2:
                spark[k] = pts
        except Exception:  # noqa: BLE001
            pass
    with _lock:
        _data["spark"].update(spark)


def _sample() -> None:
    """Lấy mẫu giá trực tiếp các mã không có nguồn lịch sử riêng (hàng hoá, tỷ giá, vàng SJC…)."""
    for k, q in live.quotes().items():
        if k.startswith(("VN:", "SEC:")):
            continue
        _samples.setdefault(k, deque(maxlen=_SAMPLE_LEN)).append(q["price"])


# ---------------------------------------------------------------------------
_PARTS = [  # (tên, hàm, chu kỳ trong phiên VN (giây), chu kỳ ngoài phiên)
    ("gold", _gold, 300, 300),
    ("bank", _bank, 300, 900),
    ("calendar", _calendar, 3600, 3600),
    ("actuals", _actuals, 600, 600),
    ("caps", _caps, 600, 600),
    ("crypto_sparks", _crypto_sparks, 300, 300),
    ("vn_days", _vn_days, 1800, 3600),
    ("vn_sparks", _vn_sparks, 300, 3600),
]


def refresh() -> None:
    session = live.vn_session_open()
    errors = {}
    with get_client() as client:
        for name, fn, every_in, every_out in _PARTS:
            if not _due(name, every_in if session else every_out):
                continue
            try:
                fn(client)
                _last[name] = time.time()
            except Exception as e:  # noqa: BLE001
                errors[name] = f"{type(e).__name__}: {str(e)[:80]}"
    _sample()
    with _lock:
        _data["errors"] = errors


def snapshot() -> dict:
    with _lock:
        d = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v)
             for k, v in _data.items()}
    d["samples"] = {k: list(v) for k, v in _samples.items()}
    return d
