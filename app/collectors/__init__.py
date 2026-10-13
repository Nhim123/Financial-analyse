"""Các collector thu thập số liệu từ từng nguồn.

Mỗi hàm collect_* trả về danh sách Reading. Mọi lỗi mạng/parse được bắt
và ghi vào Reading(ok=False) thay vì raise, để pipeline không bị gãy khi
một nguồn tạm thời không phản hồi.
"""
from .crypto import collect_coingecko, collect_binance
from .fx import collect_frankfurter, collect_erapi
from .market import collect_cnbc, collect_yahoo

__all__ = [
    "collect_coingecko",
    "collect_binance",
    "collect_frankfurter",
    "collect_erapi",
    "collect_cnbc",
    "collect_yahoo",
]
