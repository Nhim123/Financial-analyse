"""Collector thị trường (chỉ số chứng khoán + hàng hoá): CNBC và Yahoo Finance."""
from typing import List, Optional

from ..config import STOCKS, COMMODITIES
from ..models import Reading
from .http import get_client, now_iso

# Gộp chứng khoán + hàng hoá, giữ asset_class để phân loại.
_INSTRUMENTS = (
    [{**s, "asset_class": "stock"} for s in STOCKS]
    + [{**c, "asset_class": "commodity"} for c in COMMODITIES]
)


def _pct(s) -> Optional[float]:
    """'+0.45%' -> 0.45; 'UNCH'/rỗng -> None."""
    try:
        return float(str(s).replace("%", "").replace("+", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def collect_cnbc() -> List[Reading]:
    ts = now_iso()
    syms = "|".join(i["cnbc"] for i in _INSTRUMENTS)
    url = (
        "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
        f"?symbols={syms}&requestMethod=itv&exthrs=1&fund=1&output=json"
    )
    out: List[Reading] = []
    try:
        with get_client() as client:
            r = client.get(url)
            r.raise_for_status()
            quotes = r.json()["FormattedQuoteResult"]["FormattedQuote"]
        by_sym = {q.get("symbol"): q for q in quotes}
        for i in _INSTRUMENTS:
            q = by_sym.get(i["cnbc"])
            last = q.get("last") if q else None
            if not last:
                out.append(Reading(i["asset_class"], i["key"], i["name"], "cnbc", url,
                                   None, "USD", ts, ok=False, error="thiếu giá"))
            else:
                # CNBC trả chuỗi có dấu phẩy ngăn nghìn, vd "7,493.51".
                value = float(str(last).replace(",", ""))
                out.append(Reading(i["asset_class"], i["key"], i["name"], "cnbc", url,
                                   value, "USD", ts,
                                   raw=f'last={last} change_pct={q.get("change_pct")}',
                                   day_chg=_pct(q.get("change_pct"))))
    except Exception as e:  # noqa: BLE001
        for i in _INSTRUMENTS:
            out.append(Reading(i["asset_class"], i["key"], i["name"], "cnbc", url,
                               None, "USD", ts, ok=False, error=str(e)))
    return out


def collect_yahoo() -> List[Reading]:
    ts = now_iso()
    out: List[Reading] = []
    with get_client() as client:
        for i in _INSTRUMENTS:
            url = (
                f"https://query1.finance.yahoo.com/v8/finance/chart/{i['yahoo']}"
                "?interval=1d&range=1d"
            )
            try:
                r = client.get(url)
                r.raise_for_status()
                meta = r.json()["chart"]["result"][0]["meta"]
                price = meta.get("regularMarketPrice")
                if price is None:
                    out.append(Reading(i["asset_class"], i["key"], i["name"], "yahoo", url,
                                       None, "USD", ts, ok=False, error="thiếu giá"))
                else:
                    # Với range=1d, chartPreviousClose là giá đóng cửa phiên trước.
                    prev = meta.get("chartPreviousClose")
                    out.append(Reading(i["asset_class"], i["key"], i["name"], "yahoo", url,
                                       float(price), "USD", ts,
                                       raw=f'regularMarketPrice={price} chartPreviousClose={prev}',
                                       day_chg=(float(price) - prev) / prev * 100 if prev else None))
            except Exception as e:  # noqa: BLE001
                out.append(Reading(i["asset_class"], i["key"], i["name"], "yahoo", url,
                                   None, "USD", ts, ok=False, error=str(e)))
    return out
