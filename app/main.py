"""FastAPI app: trang chủ hiển thị số liệu, nguồn, kiểm chứng và tin tức; điều khiển Telegram."""
import asyncio
import gzip
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from . import (homepage, live, marketdata, news_service, notifier, recap, risk, storage, telegram_bot,
               trades)
from .config import (COLLECT_INTERVAL_SECONDS, LIVE_INTERVAL_SECONDS, MARKET_INTERVAL_SECONDS,
                     NEWS_SOURCES, SOURCES, TELEGRAM_DIGEST_HOURS, TELEGRAM_ENABLED, TELEGRAM_RECAP_HOUR)
from .pipeline import last_summary, run_pipeline

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))
scheduler = BackgroundScheduler(timezone="UTC")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Thu thập ngay khi khởi động rồi lặp theo chu kỳ.
    # Không truyền next_run_time=None: với APScheduler 3.x giá trị đó tạo job ở trạng thái TẠM DỪNG.
    scheduler.add_job(run_pipeline, "interval", seconds=COLLECT_INTERVAL_SECONDS, id="collect")
    # Giá trực tiếp: lấy ngay một lần cho trang mở đầu tiên, sau đó lặp mỗi LIVE_INTERVAL_SECONDS.
    try:
        live.refresh()
    except Exception as e:  # noqa: BLE001
        print(f"[startup] lấy giá trực tiếp lỗi: {e}")
    scheduler.add_job(live.refresh, "interval", seconds=LIVE_INTERVAL_SECONDS, id="live",
                      max_instances=1, coalesce=True,
                      next_run_time=datetime.now(timezone.utc) + timedelta(seconds=LIVE_INTERVAL_SECONDS))
    # Dữ liệu chu kỳ chậm (vàng SJC, tỷ giá ngân hàng, lịch sự kiện, sparkline): chạy ngay ở nền.
    scheduler.add_job(marketdata.refresh, "interval", seconds=MARKET_INTERVAL_SECONDS, id="market",
                      max_instances=1, coalesce=True, next_run_time=datetime.now(timezone.utc))
    # Theo dõi lệnh đang mở: đánh giá lại rủi ro, lãi/lỗ, cảnh báo cắt lỗ/chốt lời/tin bất lợi.
    scheduler.add_job(trades.monitor, "interval", seconds=120, id="trades", max_instances=1, coalesce=True)
    if TELEGRAM_ENABLED:  # nhận lệnh /danhgia, /vaolenh… từ chat Telegram của chủ tài khoản
        scheduler.add_job(telegram_bot.poll, "interval", seconds=5, id="tgbot", max_instances=1, coalesce=True)
    if TELEGRAM_ENABLED and TELEGRAM_RECAP_HOUR is not None:
        # Tóm tắt tin ngày hôm qua lúc 7:00 giờ VN; server bật muộn hơn thì gửi bù (mỗi ngày một lần).
        scheduler.add_job(recap.send_recap, "cron", hour=TELEGRAM_RECAP_HOUR, minute=0,
                          timezone="Asia/Ho_Chi_Minh", id="recap", misfire_grace_time=3 * 3600, coalesce=True)
        scheduler.add_job(recap.catch_up, "date", id="recap_catchup",
                          run_date=datetime.now(timezone.utc) + timedelta(seconds=120))
    if TELEGRAM_ENABLED and TELEGRAM_DIGEST_HOURS:
        scheduler.add_job(notifier.send_digest, "cron", hour=TELEGRAM_DIGEST_HOURS, minute=0,
                          timezone="Asia/Ho_Chi_Minh", id="digest")
    scheduler.start()
    try:
        run_pipeline()
    except Exception as e:  # noqa: BLE001
        print(f"[startup] thu thập lần đầu lỗi: {e}")
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="FinWatch — Thu thập & Kiểm chứng tài chính", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    """Trang chủ theo thiết kế 2A 'Trung tâm Tin tức & Cảnh báo' (4 tab) — dữ liệu thật."""
    run_id = storage.latest_run()
    results = storage.get_run(run_id) if run_id else []
    news = news_service.for_run(run_id)
    vm = homepage.build(results, news, news_service.market_tone(news), live.quotes(),
                        live.groups(), marketdata.snapshot(), trades.recent_alerts())
    last_run = None
    if results:
        last_run = (datetime.fromisoformat(max(r["ts"] for r in results))
                    .astimezone(notifier.VN_TZ).strftime("%H:%M %d/%m"))
    html = templates.get_template("home.html").render({
        "request": request,
        "vm": vm,
        "last_run": last_run,
        "interval_min": COLLECT_INTERVAL_SECONDS // 60,
        "telegram": notifier.status(),
        "sources": SOURCES,
        "news_sources": NEWS_SOURCES,
        "summary": last_summary(),
        "run_id": run_id,
        "journal": trades.journal_view(),
        "recap_hour": TELEGRAM_RECAP_HOUR,
        "assets": risk.asset_list(),
    })
    # Nén gzip riêng trang chủ (không dùng GZipMiddleware vì nó sẽ gom luồng SSE /stream).
    if "gzip" in request.headers.get("accept-encoding", ""):
        return Response(gzip.compress(html.encode("utf-8"), 6), media_type="text/html; charset=utf-8",
                        headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return HTMLResponse(html)


@app.get("/stream")
async def stream(request: Request):
    """Luồng Server-Sent Events: lần đầu gửi toàn bộ giá, sau đó chỉ gửi các mã vừa đổi; báo khi
    chu kỳ thu thập 10 phút xong (rv đổi) để giao diện làm mới vùng tin & cảnh báo."""
    async def events():
        last = None
        sent_v = 0
        idle = 0
        while not await request.is_disconnected():
            cur = live.versions()
            if cur != last:
                last, idle = cur, 0
                snap = live.snapshot(since=sent_v)
                sent_v = snap["v"]
                yield f"data: {json.dumps(snap, ensure_ascii=False)}\n\n"
            else:
                idle += 1
                if idle >= 15:  # nhịp giữ kết nối để mạng/proxy không tự cắt
                    idle = 0
                    yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# Đánh giá rủi ro lệnh & nhật ký lệnh (tab "Đánh giá lệnh"; cùng bộ chấm điểm với bot Telegram)
# ---------------------------------------------------------------------------
async def _body(request: Request) -> dict:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _evaluate(data: dict) -> dict:
    key = risk.resolve(data.get("symbol") or data.get("key") or "")
    return risk.evaluate(data, open_same=trades.open_count(key) if key else 0)


@app.post("/risk/evaluate")
async def risk_evaluate(request: Request):
    return _evaluate(await _body(request))


@app.post("/risk/settings")
async def risk_settings(request: Request):
    return {"ok": True, "settings": risk.save_settings(await _body(request))}


@app.post("/trades")
async def trade_create(request: Request):
    data = await _body(request)
    ev = _evaluate(data)
    if not ev.get("ok"):
        return ev
    return {**ev, "id": trades.create(ev, note=str(data.get("note") or ""), source="web")}


@app.post("/trades/{tid}/update")
async def trade_update(tid: int, request: Request):
    """Cập nhật cắt lỗ / chốt lời của lệnh đang mở (vd áp dụng gợi ý dời cắt lỗ)."""
    data = await _body(request)
    return trades.update(tid, data.get("stop"), data.get("target"))


@app.post("/trades/{tid}/close")
async def trade_close(tid: int, request: Request):
    data = await _body(request)
    return trades.close(tid, risk._f(data.get("price")))


@app.get("/api/notify/chat-id")
def notify_chat_id():
    """Sau khi nhắn /start cho bot, mở URL này để lấy TELEGRAM_CHAT_ID."""
    return notifier.discover_chats()


@app.post("/notify/test")
def notify_test():
    return notifier.send_message("✅ <b>FinWatch</b> đã kết nối Telegram thành công.\n"
                                 "Bạn sẽ nhận cảnh báo mới sau mỗi chu kỳ thu thập và bản tin định kỳ.")


@app.post("/notify/digest")
def notify_digest():
    return notifier.send_digest()


@app.get("/notify/recap/preview")
def notify_recap_preview():
    """Xem trước bản 'Tóm tắt tin hôm qua' (HTML rút gọn của Telegram) — dùng cho nút trên giao diện."""
    return {"ok": True, "html": recap.build_recap(), "telegram": TELEGRAM_ENABLED,
            "hour": TELEGRAM_RECAP_HOUR}


@app.post("/notify/recap")
def notify_recap():
    """Gửi ngay bản tóm tắt tin hôm qua lên Telegram (bấm tay — gửi cả khi hôm nay đã gửi)."""
    return recap.send_recap(force=True)


@app.get("/health")
def health():
    return {"status": "ok"}
