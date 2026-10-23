"""Tiện ích HTTP dùng chung cho các collector."""
from datetime import datetime, timezone
import httpx

from ..config import HTTP_TIMEOUT

# Một số nguồn (Yahoo) chặn request không có User-Agent giống trình duyệt.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_client(keepalive_s: float = 5.0) -> httpx.Client:
    """keepalive_s: giữ kết nối rảnh bao lâu (client dùng lâu dài cần lớn hơn chu kỳ gọi)."""
    return httpx.Client(timeout=HTTP_TIMEOUT, headers=HEADERS, follow_redirects=True,
                        limits=httpx.Limits(keepalive_expiry=keepalive_s))
