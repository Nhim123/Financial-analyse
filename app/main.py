"""FastAPI app: trang chủ hiển thị số liệu, nguồn, kiểm chứng và tin tức; điều khiển Telegram."""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from . import homepage, live, news_service, notifier, storage
from .config import (COLLECT_INTERVAL_SECONDS, LIVE_INTERVAL_SECONDS, NEWS_SOURCES, SOURCES,
                     TELEGRAM_DIGEST_HOURS, TELEGRAM_ENABLED)
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
    """Trang chủ theo thiết kế 2A 'Trung tâm Tin tức & Cảnh báo' — dữ liệu thật."""
    run_id = storage.latest_run()
    results = storage.get_run(run_id) if run_id else []
    news = news_service.for_run(run_id)
    vm = homepage.build(results, news, news_service.market_tone(news), live.quotes())
    last_run = None
    if results:
        last_run = (datetime.fromisoformat(max(r["ts"] for r in results))
                    .astimezone(notifier.VN_TZ).strftime("%H:%M %d/%m"))
    return templates.TemplateResponse("home.html", {
        "request": request,
        "vm": vm,
        "last_run": last_run,
        "interval_min": COLLECT_INTERVAL_SECONDS // 60,
        "telegram": notifier.status(),
        "sources": SOURCES,
        "news_sources": NEWS_SOURCES,
        "summary": last_summary(),
        "run_id": run_id,
    })


@app.get("/stream")
async def stream(request: Request):
    """Luồng Server-Sent Events: đẩy giá trực tiếp ngay khi có số mới, và báo khi chu kỳ
    thu thập 10 phút xong (rv đổi) để giao diện làm mới vùng tin & cảnh báo."""
    async def events():
        last = None
        idle = 0
        while not await request.is_disconnected():
            cur = live.versions()
            if cur != last:
                last, idle = cur, 0
                yield f"data: {json.dumps(live.snapshot(), ensure_ascii=False)}\n\n"
            else:
                idle += 1
                if idle >= 15:  # nhịp giữ kết nối để mạng/proxy không tự cắt
                    idle = 0
                    yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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


@app.get("/health")
def health():
    return {"status": "ok"}
