"""Collector tỷ giá: Frankfurter (ECB) và ExchangeRate-API.

Quy ước thống nhất: value = số đơn vị ngoại tệ cho 1 USD (1 USD = X CCY),
để hai nguồn so sánh được trực tiếp.
"""
from typing import List

from ..config import FX
from ..models import Reading
from .http import get_client, now_iso

_CCYS = [f["ccy"] for f in FX]


def _emit_all(source: str, url: str, ts: str, error: str) -> List[Reading]:
    return [Reading("fx", f["key"], f["name"], source, url, None, f["ccy"], ts,
                    ok=False, error=error) for f in FX]


def collect_frankfurter() -> List[Reading]:
    ts = now_iso()
    symbols = ",".join(_CCYS)
    url = f"https://api.frankfurter.app/latest?base=USD&symbols={symbols}"
    try:
        with get_client() as client:
            r = client.get(url)
            r.raise_for_status()
            rates = r.json().get("rates", {})
    except Exception as e:  # noqa: BLE001
        return _emit_all("frankfurter", url, ts, str(e))

    out: List[Reading] = []
    for f in FX:
        rate = rates.get(f["ccy"])
        if rate is None:
            # ECB không công bố một số tiền tệ (vd VND) — đánh dấu không hỗ trợ.
            out.append(Reading("fx", f["key"], f["name"], "frankfurter", url,
                               None, f["ccy"], ts, ok=False,
                               error="ECB không công bố tiền tệ này"))
        else:
            out.append(Reading("fx", f["key"], f["name"], "frankfurter", url,
                               float(rate), f["ccy"], ts, raw=f'{f["ccy"]}={rate}'))
    return out


def collect_erapi() -> List[Reading]:
    ts = now_iso()
    url = "https://open.er-api.com/v6/latest/USD"
    try:
        with get_client() as client:
            r = client.get(url)
            r.raise_for_status()
            rates = r.json().get("rates", {})
    except Exception as e:  # noqa: BLE001
        return _emit_all("erapi", url, ts, str(e))

    out: List[Reading] = []
    for f in FX:
        rate = rates.get(f["ccy"])
        if rate is None:
            out.append(Reading("fx", f["key"], f["name"], "erapi", url,
                               None, f["ccy"], ts, ok=False, error="thiếu dữ liệu"))
        else:
            out.append(Reading("fx", f["key"], f["name"], "erapi", url,
                               float(rate), f["ccy"], ts, raw=f'{f["ccy"]}={rate}'))
    return out
