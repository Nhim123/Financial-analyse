"""Collector crypto: CoinGecko và Binance (quy ra USD)."""
import json
from typing import List

from ..config import CRYPTO
from ..models import Reading
from .http import get_client, now_iso


def collect_coingecko() -> List[Reading]:
    ts = now_iso()
    ids = ",".join(c["coingecko"] for c in CRYPTO)
    url = (
        "https://api.coingecko.com/api/v3/simple/price"
        f"?ids={ids}&vs_currencies=usd&include_last_updated_at=true&include_24hr_change=true"
    )
    out: List[Reading] = []
    try:
        with get_client() as client:
            r = client.get(url)
            r.raise_for_status()
            data = r.json()
        for c in CRYPTO:
            node = data.get(c["coingecko"])
            if not node or node.get("usd") is None:
                out.append(Reading("crypto", c["key"], c["name"], "coingecko", url,
                                   None, "USD", ts, ok=False, error="thiếu dữ liệu"))
            else:
                chg = node.get("usd_24h_change")
                out.append(Reading("crypto", c["key"], c["name"], "coingecko", url,
                                   float(node["usd"]), "USD", ts,
                                   raw=json.dumps(node, ensure_ascii=False),
                                   day_chg=float(chg) if chg is not None else None))
    except Exception as e:  # noqa: BLE001
        for c in CRYPTO:
            out.append(Reading("crypto", c["key"], c["name"], "coingecko", url,
                               None, "USD", ts, ok=False, error=str(e)))
    return out


def collect_binance() -> List[Reading]:
    ts = now_iso()
    # separators không có dấu cách — tránh httpx encode " " thành %20 khiến Binance từ chối.
    symbols = json.dumps([c["binance"] for c in CRYPTO], separators=(",", ":"))
    # ticker/24hr trả cả giá hiện tại (lastPrice) lẫn % thay đổi 24h (priceChangePercent).
    url = f"https://api.binance.com/api/v3/ticker/24hr?symbols={symbols}"
    out: List[Reading] = []
    try:
        with get_client() as client:
            r = client.get(url)
            r.raise_for_status()
            data = {row["symbol"]: row for row in r.json()}
        for c in CRYPTO:
            node = data.get(c["binance"])
            if not node:
                out.append(Reading("crypto", c["key"], c["name"], "binance", url,
                                   None, "USD", ts, ok=False, error="thiếu dữ liệu"))
            else:
                # Binance niêm yết theo USDT (~USD).
                out.append(Reading("crypto", c["key"], c["name"], "binance", url,
                                   float(node["lastPrice"]), "USD", ts,
                                   raw=json.dumps({k: node.get(k) for k in
                                                   ("symbol", "lastPrice", "priceChangePercent")}),
                                   day_chg=float(node["priceChangePercent"])))
    except Exception as e:  # noqa: BLE001
        for c in CRYPTO:
            out.append(Reading("crypto", c["key"], c["name"], "binance", url,
                               None, "USD", ts, ok=False, error=str(e)))
    return out
