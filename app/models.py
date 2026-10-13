"""Cấu trúc dữ liệu chung cho một lần đọc số liệu (Reading) và một tin (NewsItem)."""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Reading:
    asset_class: str          # crypto | fx | stock | commodity
    key: str                  # mã chuẩn hoá, vd: BTC, SPX, XAU
    name: str                 # tên hiển thị
    source: str               # mã nguồn, vd: coingecko
    source_url: str           # URL chính xác đã gọi (để truy vết / kiểm chứng)
    value: Optional[float]    # giá trị thu được (None nếu lỗi)
    quote: str = "USD"        # đơn vị định giá
    ts: str = ""              # thời điểm thu thập (ISO, UTC)
    ok: bool = True           # gọi nguồn thành công hay không
    error: str = ""           # mô tả lỗi nếu có
    raw: str = ""             # trích đoạn dữ liệu thô để đối chứng
    day_chg: Optional[float] = None  # % thay đổi trong phiên (crypto: 24h) do nguồn công bố


@dataclass
class NewsItem:
    source: str                       # mã hãng tin, vd: ft
    title: str                        # tiêu đề
    link: str                         # URL bài viết (truy vết)
    published: Optional[str]          # thời điểm đăng (ISO, UTC) nếu có
    summary: str = ""                 # mô tả ngắn
    classes: List[str] = field(default_factory=list)  # lĩnh vực liên quan
    fetched_at: str = ""              # thời điểm thu thập (ISO, UTC)
