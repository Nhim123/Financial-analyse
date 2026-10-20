"""Gửi thông báo Telegram: cảnh báo tức thời sau mỗi lần thu thập + bản tin định kỳ.

Chống spam: mỗi cảnh báo có một khoá; khoá đã gửi được lưu ở bảng `notifications`
nên cùng một sự kiện không bị gửi lại ở các chu kỳ sau. Khoá chỉ được ghi khi
Telegram xác nhận gửi thành công -> gửi lỗi thì chu kỳ sau tự thử lại.
"""
import hashlib
import html
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import httpx

from . import news_service, storage
from .enrich import market_importance
from .config import (ANOMALY_THRESHOLD_PCT, COLLECT_INTERVAL_SECONDS, DASHBOARD_URL, DIFF_THRESHOLD_PCT,
                     MOVE_ALERT_PCT, SPIKE_ALERT_PCT,
                     TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_DIGEST_HOURS,
                     TELEGRAM_ENABLED, TELEGRAM_MAX_NEWS_PER_ALERT, TELEGRAM_NEWS_MAX_AGE_HOURS,
                     TELEGRAM_NEWS_GROUPS, TELEGRAM_NEWS_MODE, TELEGRAM_NEWS_REQUIRE_VERIFIED,
                     TELEGRAM_PROXY, TELEGRAM_SILENT_HOURS)

# Một cảnh báo = (danh sách khoá chống trùng, nội dung). Khoá đầu tiên quyết định đã gửi hay chưa;
# mọi khoá được đánh dấu khi gửi thành công.
Alert = Tuple[List[str], str]
from .homepage import (TAG_MAP, ZONE_META, ZONE_ORDER, _SHORT_NAMES as SHORT_NAMES,
                       _fmt as fmt_value, _vn_time)

VN_TZ = timezone(timedelta(hours=7))
_MAX_LEN = 4000  # Telegram giới hạn 4096 ký tự / tin nhắn

_last_error: Optional[str] = None
_last_sent: Optional[str] = None

_TONE_CLASS_LABEL = {"hawk": "Hawkish", "dove": "Dovish", "bal": "Cân bằng"}

_DIGEST_GROUPS = [
    ("📈 Chứng khoán", ["SPX", "DJI", "NDQ", "FTSE", "N225", "DAX"]),
    ("🛢️ Hàng hoá", ["XAU", "WTI", "BRENT"]),
    ("🪙 Crypto", ["BTC", "ETH", "BNB", "SOL", "XRP"]),
    ("💱 Tỷ giá (1 USD =)", ["USDEUR", "USDGBP", "USDJPY", "USDCNY", "USDVND"]),
]


# ---------------------------------------------------------------------------
# Gửi tin
# ---------------------------------------------------------------------------

def _api(method: str) -> str:
    return f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"


def _client() -> httpx.Client:
    return httpx.Client(timeout=20, proxy=TELEGRAM_PROXY)


def _redact(s: str) -> str:
    """Không để token lọt vào log / API trạng thái."""
    return s.replace(TELEGRAM_BOT_TOKEN, "***") if TELEGRAM_BOT_TOKEN else s


def _esc(s) -> str:
    return html.escape(str(s or ""), quote=False)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _vn_now() -> str:
    return datetime.now(VN_TZ).strftime("%d/%m/%Y %H:%M")


def _short(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _chunks(text: str) -> List[str]:
    """Cắt tin dài theo dòng để không vượt giới hạn của Telegram."""
    if len(text) <= _MAX_LEN:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > _MAX_LEN:
            parts.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        parts.append(cur)
    return parts


def _silent_now() -> bool:
    """Đang trong giờ yên lặng (TELEGRAM_SILENT_HOURS, giờ VN, vd "23-6")?"""
    try:
        start, end = (int(x) for x in TELEGRAM_SILENT_HOURS.split("-"))
    except ValueError:
        return False
    h = datetime.now(VN_TZ).hour
    return (h >= start or h < end) if start > end else (start <= h < end)


def send_message(text: str, silent: bool = False) -> dict:
    global _last_error, _last_sent
    if not TELEGRAM_ENABLED:
        return {"ok": False,
                "error": "Telegram chưa cấu hình (thiếu TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID trong .env)."}
    try:
        with _client() as c:
            for part in _chunks(text):
                r = c.post(_api("sendMessage"), json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": part,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                    "disable_notification": silent,
                })
                data = r.json()
                if not data.get("ok"):
                    _last_error = _redact(data.get("description", f"HTTP {r.status_code}"))
                    return {"ok": False, "error": _last_error}
    except Exception as e:  # noqa: BLE001 — lỗi mạng/proxy không được làm gãy pipeline
        _last_error = _redact(f"{type(e).__name__}: {e}")
        return {"ok": False, "error": _last_error}
    _last_error = None
    _last_sent = _now_utc()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Cảnh báo tức thời
# ---------------------------------------------------------------------------

def _name(r: dict) -> str:
    return _esc(SHORT_NAMES.get(r["key"], r["name"]))


def _period(r: dict) -> str:
    # Crypto giao dịch 24/7 (nguồn công bố % 24h); tỷ giá tự tính so với ~24h trước.
    return "trong 24h" if r["asset_class"] in ("crypto", "fx") else "trong phiên"


def _move_alerts(results: List[dict]) -> List[Alert]:
    """Biến động mạnh theo % thay đổi trong phiên. Mỗi bậc (1×, 2×, 3× ngưỡng) báo một lần/ngày."""
    today = datetime.now(VN_TZ).strftime("%Y%m%d")
    out = []
    for r in sorted(results, key=lambda r: -abs((r.get("day_chg_pct") or 0)
                                               / MOVE_ALERT_PCT.get(r["asset_class"], 1e9))):
        chg, thr = r.get("day_chg_pct"), MOVE_ALERT_PCT.get(r["asset_class"])
        if chg is None or not thr or abs(chg) < thr:
            continue
        tier = int(abs(chg) // thr)
        side = "up" if chg > 0 else "down"
        # Khoá bậc cao nhất đứng đầu; gửi xong đánh dấu cả các bậc thấp hơn.
        keys = [f"move:{r['key']}:{today}:{side}:{t}" for t in range(tier, 0, -1)]
        text = (f"{'🚀' if chg > 0 else '📉'} <b>{_name(r)} {chg:+.2f}%</b> {_period(r)} "
                f"— giá {fmt_value(r['consensus'])}")
        text += f" · <b>gấp {tier} lần</b> ngưỡng ±{thr}%" if tier >= 2 else f" (ngưỡng ±{thr}%)"
        out.append((keys, text))
    return out


def _spike_alerts(results: List[dict]) -> List[Alert]:
    """Đột biến trong ~1 giờ, so với lần thu thập cách 45–90 phút (bỏ qua nếu không có)."""
    hour = datetime.now(VN_TZ).strftime("%Y%m%d%H")
    out = []
    for r in results:
        thr = SPIKE_ALERT_PCT.get(r["asset_class"])
        if r["consensus"] is None or not thr:
            continue
        t = datetime.fromisoformat(r["ts"])
        ref = storage.consensus_between(r["key"],
                                        (t - timedelta(minutes=90)).isoformat(timespec="seconds"),
                                        (t - timedelta(minutes=45)).isoformat(timespec="seconds"))
        if not ref:
            continue
        p = (r["consensus"] - ref) / ref * 100
        if abs(p) >= thr:
            out.append(([f"spike:{r['key']}:{hour}"],
                        f"⚡ <b>{_name(r)} {p:+.2f}%</b> trong ~1 giờ "
                        f"— giá {fmt_value(r['consensus'])} (ngưỡng ±{thr}%)"))
    return out


def _quality_alerts(results: List[dict]) -> List[Alert]:
    """Sự cố về chất lượng dữ liệu (mất nguồn, nguồn lệch nhau, nhảy giá bất thường)."""
    now = datetime.now(VN_TZ)
    day, hour = now.strftime("%Y%m%d"), now.strftime("%Y%m%d%H")
    out = []
    for r in results:
        anom = ANOMALY_THRESHOLD_PCT.get(r["asset_class"], 10.0)
        diff = DIFF_THRESHOLD_PCT.get(r["asset_class"], 1.0)
        if r["status"] == "ERROR":
            out.append(([f"data:{r['key']}:error:{day}"],
                        f"⛔ <b>KHẨN</b> · {_name(r)}: không nguồn nào trả dữ liệu"))
        elif r["change_pct"] is not None and abs(r["change_pct"]) >= anom:
            out.append(([f"data:{r['key']}:anomaly:{hour}"],
                        f"⛔ <b>KHẨN</b> · {_name(r)} nhảy {r['change_pct']:+.2f}% chỉ sau một chu kỳ "
                        f"thu thập — có thể là lỗi dữ liệu, cần kiểm tra"))
        elif r["num_sources"] >= 2 and (r["max_diff_pct"] or 0) > diff:
            out.append(([f"data:{r['key']}:diff:{day}"],
                        f"⚠️ <b>CHÚ Ý</b> · {_name(r)}: các nguồn lệch {r['max_diff_pct']:.2f}% "
                        f"(ngưỡng {diff}%)"))
        # Trường hợp "chỉ 1 nguồn" (vd USD/VND) là trạng thái thường trực -> không gửi.
    return out


def _news_line(n: dict, tag: str, bullet: str = "🔴") -> str:
    imp = n["impact"]
    meta = [tag, _esc(n["source_label"])]
    if n["verified"]:
        meta.append(f"✓ {n['corroboration_count']} nguồn")
    meta.append(f"{imp['direction_sym']} {imp['direction']}")
    if n["tone"]["tone"] in ("hawkish", "dovish"):
        meta.append(f"{n['tone']['sym']} {n['tone']['label']}")
    link = html.escape(n["link"], quote=True)
    line = f"{bullet} <a href=\"{link}\">{_esc(n['vi_title'])}</a>\n    <i>{' · '.join(meta)}</i>"
    if n.get("vi_summary"):
        line += f"\n    {_esc(_short(n['vi_summary'], 160))}"
    return line


def _unique_news(news: Dict[str, dict]) -> List[Tuple[dict, str]]:
    seen, out = set(), []
    for cls, block in news.items():
        tag = TAG_MAP.get(cls, ("Tin",))[0]
        for n in block["items"]:
            if n["link"] not in seen:
                seen.add(n["link"])
                out.append((n, tag))
    return out


def _news_key(link: str) -> str:
    return "news:" + hashlib.sha256(link.encode("utf-8")).hexdigest()[:16]


def _news_candidates(news: Dict[str, dict]) -> List[Tuple[str, dict]]:
    """(nhóm, tin) thoả bộ lọc gửi Telegram: đúng nhóm đã chọn, mới đăng, đúng chế độ."""
    seen, out = set(), []
    for cls in ZONE_ORDER:
        if TELEGRAM_NEWS_GROUPS and cls not in TELEGRAM_NEWS_GROUPS:
            continue
        for n in news.get(cls, {}).get("items", []):
            age = n["age_hours"]
            if (n["link"] in seen or age is None or age > TELEGRAM_NEWS_MAX_AGE_HOURS
                    or (TELEGRAM_NEWS_MODE != "all" and n["impact"]["level"] < 3)
                    or (TELEGRAM_NEWS_REQUIRE_VERIFIED and not n["verified"])):
                continue
            seen.add(n["link"])  # tin thuộc nhiều nhóm chỉ hiện ở nhóm đầu tiên
            out.append((cls, n))
    return out


def _news_batch(news: Dict[str, dict]) -> Tuple[List[Tuple[str, dict]], List[str]]:
    """Chọn tin MỚI cho một tin nhắn và danh sách khoá sẽ đánh dấu đã gửi.

    Tin đã xác nhận & ảnh hưởng mạnh được ưu tiên; tin dư chưa đánh dấu nên sang chu kỳ sau.
    Lần đầu bật chức năng: chỉ gửi các tin ưu tiên nhất, phần tồn đọng còn lại đánh dấu luôn
    để không bị gửi dồn ở các chu kỳ sau.
    """
    cands = _news_candidates(news)
    unsent = set(storage.filter_unsent([_news_key(n["link"]) for _, n in cands]))
    fresh = [c for c in cands if _news_key(c[1]["link"]) in unsent]
    fresh.sort(key=lambda c: (-int(c[1]["verified"]), -c[1]["impact"]["level"], c[1]["age_hours"]))
    batch = fresh[:TELEGRAM_MAX_NEWS_PER_ALERT]
    marked = fresh if storage.kv_get("news_notify_started") is None else batch
    return batch, [_news_key(n["link"]) for _, n in marked]


def _news_message(batch: List[Tuple[str, dict]]) -> str:
    """Tin nhắn "Tin tức mới": chia theo nhóm như màn hình chính, mới nhất trước trong nhóm."""
    lines = ["📰 <b>FinWatch — Tin tức mới</b>", f"🕒 {_vn_now()} (giờ VN) · {len(batch)} tin"]
    for cls in ZONE_ORDER:
        group = sorted((n for c, n in batch if c == cls), key=lambda n: n["age_hours"])
        if not group:
            continue
        icon, title = ZONE_META[cls]
        lines += ["", f"{icon} <b>{_esc(title)}</b> ({len(group)})"]
        for n in group:
            imp = n["impact"]
            meta = [_vn_time(n["published"], n["age_hours"]), _esc(n["source_label"])]
            if n["verified"]:
                meta.append(f"✓ {n['corroboration_count']} nguồn")
            if imp["level"] >= 2:
                meta.append(f"{imp['direction_sym']} {imp['direction']}")
            link = html.escape(n["link"], quote=True)
            lines.append(f"{imp['level_sym']} <a href=\"{link}\">{_esc(n['vi_title'])}</a>\n"
                         f"    <i>{' · '.join(meta)}</i>")
            summary = n.get("vi_summary") or n.get("summary")
            if summary:
                lines.append(f"    {_esc(_short(summary, 160))}")
    lines += ["", f"🔗 Xem tất cả: {_esc(DASHBOARD_URL)}"]
    return "\n".join(lines)


def _fresh(alerts: List[Alert]) -> List[Alert]:
    unsent = set(storage.filter_unsent([keys[0] for keys, _ in alerts]))
    return [a for a in alerts if a[0][0] in unsent]


def _tone_alert(tone: Optional[dict]) -> Optional[Tuple[str, str]]:
    """Báo khi kết luận Dovish/Hawkish đổi chiều (bỏ qua khi quá ít tín hiệu)."""
    if not tone or tone["confidence"] in ("none", "low"):
        return None
    cls = tone["verdict_class"]
    prev = storage.kv_get("tone_class")
    if prev == cls:
        return None
    head = ("Giọng điệu chính sách hiện tại" if prev is None
            else f"Giọng điệu chính sách đổi từ {_TONE_CLASS_LABEL.get(prev, prev)} sang")
    net = f" (net {tone['net']:+d}/±100)" if tone["net"] is not None else ""
    line = (f"{tone['verdict_sym']} {head}: <b>{_esc(tone['verdict'])}</b>{net}\n"
            f"    🕊️ {tone['pct_dovish']}% · Trung lập {tone['pct_neutral']}% · 🦅 {tone['pct_hawkish']}%"
            f" — {_esc(tone['confidence_label'])}")
    return cls, line


def notify_run(run_id: str, results: List[dict]) -> dict:
    """Gọi sau mỗi chu kỳ thu thập. Gửi tối đa 2 tin nhắn:
    1) "Biến động & cảnh báo" (giá, chính sách tiền tệ, chất lượng dữ liệu);
    2) "Tin tức mới" theo nhóm. Mỗi tin nhắn đánh dấu đã gửi độc lập khi Telegram xác nhận.
    """
    if not TELEGRAM_ENABLED:
        return {"sent": False, "reason": "disabled"}

    news = news_service.for_run(run_id)
    tone = news_service.market_tone(news)
    silent = _silent_now()
    out = {"silent": silent}

    moves = _fresh(_move_alerts(results) + _spike_alerts(results))
    quality = _fresh(_quality_alerts(results))
    tone_alert = _tone_alert(tone)
    if moves or quality or tone_alert:
        lines = ["🚨 <b>FinWatch — Biến động &amp; cảnh báo</b>", f"🕒 {_vn_now()} (giờ VN)"]
        if moves:
            lines += ["", "<b>📈📉 Biến động mạnh</b>"] + [t for _, t in moves]
        if tone_alert:
            lines += ["", "<b>🦅🕊️ Chính sách tiền tệ</b>", tone_alert[1]]
        if quality:
            lines += ["", "<b>🛡️ Kiểm chứng dữ liệu</b>"] + [t for _, t in quality]
        lines += ["", f"🔗 {_esc(DASHBOARD_URL)}"]
        res = send_message("\n".join(lines), silent=silent)
        if res["ok"]:
            storage.mark_sent([k for keys, _ in moves + quality for k in keys], _now_utc())
            if tone_alert:
                storage.kv_set("tone_class", tone_alert[0])
        out.update(alerts_sent=res["ok"], alerts_error=res.get("error"), move_alerts=len(moves),
                   quality_alerts=len(quality), tone_alert=bool(tone_alert))

    batch, to_mark = _news_batch(news)
    if batch:
        res = send_message(_news_message(batch), silent=silent)
        if res["ok"]:
            storage.mark_sent(to_mark, _now_utc())
            if storage.kv_get("news_notify_started") is None:
                storage.kv_set("news_notify_started", _now_utc())
        out.update(news_sent=res["ok"], news_error=res.get("error"), news_items=len(batch),
                   news_skipped_backlog=len(to_mark) - len(batch))

    out["sent"] = bool(out.get("alerts_sent") or out.get("news_sent"))
    if len(out) == 2:
        out["reason"] = "no_new_alerts"
    return out


# ---------------------------------------------------------------------------
# Bản tin định kỳ
# ---------------------------------------------------------------------------

def build_digest() -> Optional[str]:
    run_id = storage.latest_run()
    if not run_id:
        return None
    results = storage.get_run(run_id)
    by_key = {r["key"]: r for r in results}
    news = news_service.for_run(run_id)
    tone = news_service.market_tone(news)
    run_dt = datetime.fromisoformat(max(r["ts"] for r in results))

    lines = ["📊 <b>FinWatch — Bản tin thị trường</b>", f"🕒 {_vn_now()} (giờ VN)"]
    age_min = (datetime.now(timezone.utc) - run_dt).total_seconds() / 60
    if age_min > 2 * COLLECT_INTERVAL_SECONDS / 60:
        lines.append(f"⚠️ <b>Dữ liệu cũ</b> — cập nhật lần cuối "
                     f"{run_dt.astimezone(VN_TZ).strftime('%d/%m %H:%M')} (server có thể đã dừng thu thập)")

    # Biến động đáng chú ý nhất: xếp theo độ lớn so với ngưỡng của từng nhóm tài sản.
    ranked = sorted((r for r in results if r.get("day_chg_pct") is not None),
                    key=lambda r: -abs(r["day_chg_pct"]) / MOVE_ALERT_PCT.get(r["asset_class"], 1e9))[:3]
    if ranked:
        lines += ["", "🔥 <b>Biến động đáng chú ý:</b> " + " · ".join(
            f"{_name(r)} {'▲' if r['day_chg_pct'] >= 0 else '▼'}{r['day_chg_pct']:+.2f}%" for r in ranked)]

    for title, keys in _DIGEST_GROUPS:
        rows = []
        for k in keys:
            r = by_key.get(k)
            if not r or r["consensus"] is None:
                continue
            chg = r.get("day_chg_pct")
            row = f"• {_name(r)}: <b>{fmt_value(r['consensus'])}</b>"
            if chg is not None:
                row += f" {'▲' if chg >= 0 else '▼'} {chg:+.2f}%"
            rows.append(row)
        if rows:
            lines += ["", f"<b>{title}</b>"] + rows

    ok = sum(1 for r in results if r["status"] == "OK")
    warn = sum(1 for r in results if r["status"] == "WARN")
    err = sum(1 for r in results if r["status"] == "ERROR")
    lines += ["", f"✅ <b>Kiểm chứng:</b> {ok}/{len(results)} đạt · {warn} cảnh báo · {err} lỗi"]

    if tone:
        net = f" (net {tone['net']:+d}/±100)" if tone["net"] is not None else ""
        lines += ["", "<b>🦅🕊️ Chính sách tiền tệ</b>",
                  f"{tone['verdict_sym']} {_esc(tone['verdict'])}{net}",
                  f"🕊️ {tone['pct_dovish']}% · Trung lập {tone['pct_neutral']}% · 🦅 {tone['pct_hawkish']}%"
                  f" — {_esc(tone['confidence_label'])}"]

    # Chỉ tin quan trọng, ảnh hưởng lớn tới thị trường (điểm market_importance ≥ REPORT_MIN_IMPORTANCE).
    scored = [(market_importance(x[0]), x) for x in _unique_news(news) if not x[0]["stale"]]
    top = [x for _, x in sorted((s for s in scored if s[0]["important"]), key=lambda s: (
        -s[0]["score"], s[1][0]["age_hours"] if s[1][0]["age_hours"] is not None else 9999))][:5]
    if top:
        lines += ["", "<b>📰 Tin quan trọng ảnh hưởng thị trường</b>"]
        for n, tag in top:
            bullet = {3: "🔴", 2: "🟠"}.get(n["impact"]["level"], "⚪")
            lines.append(_news_line(n, tag, bullet))

    lines += ["", "<i>% thay đổi trong phiên (crypto, tỷ giá: 24 giờ) · dữ liệu tham khảo, "
                  "không phải khuyến nghị đầu tư</i>",
              f"🔗 {_esc(DASHBOARD_URL)}"]
    return "\n".join(lines)


def send_digest() -> dict:
    text = build_digest()
    if not text:
        return {"ok": False, "error": "Chưa có dữ liệu để lập bản tin."}
    return send_message(text)


# ---------------------------------------------------------------------------
# Trạng thái & hỗ trợ cấu hình
# ---------------------------------------------------------------------------

def status() -> dict:
    cid = TELEGRAM_CHAT_ID
    return {
        "enabled": TELEGRAM_ENABLED,
        "has_token": bool(TELEGRAM_BOT_TOKEN),
        "chat_id": (("…" + cid[-4:]) if len(cid) > 4 else cid) or None,
        "proxy": bool(TELEGRAM_PROXY),
        "digest_hours": TELEGRAM_DIGEST_HOURS or None,
        "news_require_verified": TELEGRAM_NEWS_REQUIRE_VERIFIED,
        "last_sent": _last_sent,
        "last_error": _last_error,
    }


def discover_chats() -> dict:
    """Liệt kê các cuộc trò chuyện đã nhắn cho bot — để lấy TELEGRAM_CHAT_ID. Chỉ cần token."""
    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "Chưa có TELEGRAM_BOT_TOKEN trong .env."}
    try:
        with _client() as c:
            me = c.get(_api("getMe")).json()
            upd = c.get(_api("getUpdates")).json()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": _redact(f"{type(e).__name__}: {e}")}
    if not me.get("ok"):
        return {"ok": False, "error": _redact(me.get("description", "token không hợp lệ"))}
    chats = {}
    for u in upd.get("result", []):
        msg = u.get("message") or u.get("channel_post") or u.get("my_chat_member") or {}
        chat = msg.get("chat")
        if chat:
            name = (chat.get("title")
                    or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
                    or chat.get("username"))
            chats[chat["id"]] = {"chat_id": chat["id"], "type": chat.get("type"), "name": name}
    out = {"ok": True, "bot": "@" + me["result"].get("username", "?"), "chats": list(chats.values())}
    if not chats:
        out["hint"] = ("Chưa thấy cuộc trò chuyện nào. Mở Telegram, nhắn /start cho bot "
                       "(hoặc thêm bot vào nhóm rồi nhắn một tin), sau đó tải lại trang này.")
    return out
