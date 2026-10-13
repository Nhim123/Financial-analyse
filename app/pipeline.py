"""Điều phối: thu thập -> kiểm chứng -> lưu trữ."""
import threading
import uuid
from datetime import datetime, timezone
from typing import List

from . import live, news_service, notifier, storage
from .collectors import (collect_binance, collect_coingecko, collect_erapi,
                         collect_frankfurter, collect_cnbc, collect_yahoo)
from .collectors.news import collect_news
from .collectors.http import get_client, now_iso
from .enrich import translate_vi
from .models import Reading
from .verify import verify_all

_run_lock = threading.Lock()
_last_summary: dict = {}

COLLECTORS = [
    collect_coingecko,
    collect_binance,
    collect_frankfurter,
    collect_erapi,
    collect_cnbc,
    collect_yahoo,
]


def run_pipeline() -> dict:
    """Chạy một chu kỳ thu thập + kiểm chứng. Trả về tóm tắt."""
    global _last_summary
    with _run_lock:
        run_id = uuid.uuid4().hex[:12]
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")

        readings: List[Reading] = []
        for fn in COLLECTORS:
            try:
                readings.extend(fn())
            except Exception as e:  # noqa: BLE001 — collector tự bắt lỗi, đây là lớp chặn cuối
                print(f"[pipeline] collector {fn.__name__} lỗi: {e}")

        storage.save_readings(run_id, readings)

        results = verify_all(readings)
        storage.save_verifications(run_id, results)

        # --- Tin tức ---
        news_count = 0
        try:
            news = collect_news()
            storage.save_news(run_id, news)
            news_count = len(news)
            _translate_displayed(news)
        except Exception as e:  # noqa: BLE001
            print(f"[pipeline] thu thập/dịch tin lỗi: {e}")

        summary = {
            "run_id": run_id,
            "ts": ts,
            "total_keys": len(results),
            "ok": sum(1 for r in results if r["status"] == "OK"),
            "warn": sum(1 for r in results if r["status"] == "WARN"),
            "error": sum(1 for r in results if r["status"] == "ERROR"),
            "total_readings": len(readings),
            "news_count": news_count,
        }

        # --- Thông báo Telegram (tự bỏ qua nếu chưa cấu hình) ---
        try:
            summary["telegram"] = notifier.notify_run(run_id, results)
        except Exception as e:  # noqa: BLE001 — lỗi gửi thông báo không được làm hỏng chu kỳ thu thập
            print(f"[pipeline] gửi Telegram lỗi: {e}")

        _last_summary = summary
        live.bump_run()  # báo giao diện đang mở làm mới vùng tin & cảnh báo
        print(f"[pipeline] run {run_id}: {summary}")
        return summary


def last_summary() -> dict:
    return _last_summary


def _translate_displayed(news_items) -> None:
    """Dịch sẵn (và cache) tiêu đề + tóm tắt của các tin SẼ hiển thị.

    Chỉ dịch tin nằm trong top mỗi lĩnh vực và chưa có bản dịch -> tiết kiệm
    lượt gọi và tránh dịch lại mỗi lần tải trang.
    """
    dicts = [{"source": n.source, "title": n.title, "link": n.link,
              "published": n.published, "summary": n.summary, "classes": n.classes}
             for n in news_items]
    built = news_service.build(dicts)  # chưa có bản dịch -> để chọn tin hiển thị

    # Gom các tin hiển thị (loại trùng theo link).
    display = {}
    for block in built.values():
        for it in block["items"]:
            display.setdefault(it["link"], it)

    have = storage.get_translations(list(display.keys()))
    todo = [it for link, it in display.items() if link not in have]
    if not todo:
        return

    records = []
    ts = now_iso()
    with get_client() as client:
        for it in todo:
            vi_title = translate_vi(it["title"], client)
            vi_summary = translate_vi(it["summary"], client) if it.get("summary") else ""
            records.append({"link": it["link"], "vi_title": vi_title,
                            "vi_summary": vi_summary, "updated_at": ts})
    storage.upsert_translations(records)
    print(f"[pipeline] đã dịch {len(records)} tin mới")
